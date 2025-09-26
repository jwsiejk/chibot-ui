# app/ws/ws_asgi.py — Phase 2+ (Deepgram wired; WS protocol + delegation; WS-only greet + typed turns)
from __future__ import annotations
import asyncio, os, contextlib, time, io, struct, base64
from typing import Optional, Dict, Any, Deque, Callable, Awaitable, List, Tuple
from collections import deque
from app.services.audio.container_sniffer import AudioContainerSniffer, coerce_detection_from_meta

from .schema_v1 import parse_client_json, make_keepalive_ack, make_results, make_utterance_end, make_error
from .turn_buffer import TurnBuffer
from app.services.streaming_asr.deepgram_client import DeepgramClient
from app.security.ws_token import verify as verify_ws_token
from app.db import db
from app.metrics import ws_metrics
# NEW: invoke LLM on final transcript
from app.services.streaming import run_ws_user_turn  # NEW

# Optional admin emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    _admin_emit = None

WS_ASGI_BUILD = "miccap-v4"  # bump when you redeploy
try:
    _jlog("ws_asgi_build", build=WS_ASGI_BUILD, pid=os.getpid())
except Exception:
    pass

# ------------------------------ small helpers ------------------------------

def _client_ip_from_scope(scope) -> str:
    try:
        c = scope.get("client") or ()
        if isinstance(c, (list, tuple)) and c:
            return str(c[0])
    except Exception:
        pass
    try:
        hdrs = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        xff = hdrs.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    except Exception:
        pass
    return "unknown"


def _jlog(event: str, **fields):
    """Lightweight JSON log (stdout). Keep dependency-free inside WS path."""
    try:
        import time as _t, json as _json
        fields.setdefault("event", event)
        fields.setdefault("ts", _t.time())
        print(_json.dumps(fields, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        pass


def _clip_text(txt: str, limit: int = 120) -> str:
    try:
        txt = txt or ""
        if len(txt) <= limit:
            return txt
        return txt[:limit] + "…"
    except Exception:
        return ""


def _dumps(obj) -> str:
    import json as _json
    return _json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _get_session_id(scope) -> str:
    try:
        raw = (scope.get("query_string") or b"").decode("utf-8", "ignore")
        if raw:
            for pair in raw.split("&"):
                if not pair or "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                if k == "session_id":
                    return v or "default"
    except Exception:
        pass
    return "default"


def _has_deepgram_key() -> bool:
    return bool((os.getenv("DEEPGRAM_API_KEY") or "").strip())


def _env_truth(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _wav_with_header(pcm: bytes, sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    """Wrap raw PCM in a minimal RIFF/WAVE header for easy playback."""
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    datasz = len(pcm)
    riffsz = 36 + datasz
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", riffsz))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample))
    buf.write(b"data")
    buf.write(struct.pack("<I", datasz))
    buf.write(pcm)
    return buf.getvalue()


async def _ws_send_json(send, obj: dict) -> None:
    await send({"type": "websocket.send", "text": _dumps(obj)})


async def _ws_send_diagnostic_audio(send, turn_id: int, mime: str, data: bytes) -> None:
    """
    Send the captured mic audio back to the client so you can play it.
    Uses chunked base64 to avoid giant WS frames.
    """
    CHUNK = 64 * 1024  # 64 KiB raw → ~85 KiB b64
    total = len(data)
    off = 0
    part = 0
    # announce
    await _ws_send_json(send, {
        "type": "diagnostic_audio",
        "turn_id": str(turn_id),
        "mime": mime,
        "total_bytes": total,
        "part": part,
        "is_last": (total == 0),
        "b64": ""  # header-only announcement
    })
    while off < total:
        chunk = data[off: off + CHUNK]
        off += len(chunk)
        part += 1
        await _ws_send_json(send, {
            "type": "diagnostic_audio",
            "turn_id": str(turn_id),
            "mime": mime,
            "total_bytes": total,
            "part": part,
            "is_last": (off >= total),
            "b64": base64.b64encode(chunk).decode("ascii")
        })


# ------------------------------ bus pumpers ------------------------------

async def _pump_bus_to_client(sid: str, send):
    """Forward frames from StreamBus to the WS client as JSON."""
    import json as _json
    from queue import Empty
    from app.ws.bus import bus

    q = bus.subscribe(sid)
    try:
        while True:
            try:
                fr = q.get(timeout=0.05)
            except Empty:
                await asyncio.sleep(0.01)
                continue
            try:
                await send({"type": "websocket.send", "text": _json.dumps(fr, separators=(",", ":"), ensure_ascii=False)})
            except Exception:
                await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            if hasattr(bus, "unsubscribe"):
                bus.unsubscribe(sid, q)
        except Exception:
            pass


async def _pump_dg_to_client(
    dg: DeepgramClient,
    send,
    turn_id_ref,
    final_seen,
    sid: str,
    asr_ready_evt: Optional[asyncio.Event] = None,
    on_asr_open_flush: Optional[Callable[[], Awaitable[None]]] = None,
):
    """Relay Deepgram events to client and, on final, kick LLM turn."""
    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                _jlog("dg_asr_open", sid=sid)
                try:
                    if asr_ready_evt and not asr_ready_evt.is_set():
                        asr_ready_evt.set()
                except Exception:
                    pass
                try:
                    _admin_emit and _admin_emit("asr:start", session_id=sid)
                except Exception:
                    pass
                try:
                    if on_asr_open_flush:
                        await asyncio.sleep(0.05)
                        await on_asr_open_flush()
                except Exception:
                    pass
                continue

            if et in ("user_partial", "user_final"):
                is_final = (et == "user_final")
                text = (ev.get("text") or "").strip()
                _jlog("dg_transcript", sid=sid, turn_id=turn_id_ref[0], is_final=is_final, chars=len(text), preview=_clip_text(text))
                try:
                    if et == "user_partial":
                        _admin_emit and _admin_emit("asr:first_partial", session_id=sid)
                except Exception:
                    pass

                await _ws_send_json(send, make_results(turn_id_ref[0], transcript=text, confidence=0.0, is_final=is_final))

                if is_final:
                    final_seen[0] = True
                    await _ws_send_json(send, make_utterance_end(turn_id_ref[0]))
                    try:
                        _admin_emit and _admin_emit("asr:final", session_id=sid)
                    except Exception:
                        pass

                    if text:
                        async def _bg_turn():
                            try:
                                await asyncio.to_thread(run_ws_user_turn, sid, text, None)
                            except Exception as e:
                                with contextlib.suppress(Exception):
                                    await _ws_send_json(send, make_error("llm_turn_fail", e.__class__.__name__))
                        asyncio.create_task(_bg_turn())

            elif et == "asr_error":
                err = _clip_text(str(ev.get("error") or "unknown"), 160)
                _jlog("dg_asr_error", sid=sid, turn_id=turn_id_ref[0], error=err)
                try:
                    _admin_emit and _admin_emit("asr:error", session_id=sid, error=err)
                except Exception:
                    pass
                await _ws_send_json(send, make_error("asr_error", err))
    except asyncio.CancelledError:
        return
    except Exception as e:
        with contextlib.suppress(Exception):
            await _ws_send_json(send, make_error("relay_fail", e.__class__.__name__))


# ------------------------------ main WS impl ------------------------------

async def _ws_chat_asgi_impl(scope, receive, send):
    # Session-scoped transport flags for ASR
    transport = {"protocol":"websocket","container":None,"codec":None,"containerized_opus":False,"features":[]}
    sniffer = AudioContainerSniffer()
    audio_sig_logged = False

    MIC_CAPTURE = _env_truth("MIC_CAPTURE", False)
    MIC_ECHO_WS = _env_truth("MIC_ECHO_WS", False)

    _jlog("mic_capture_cfg", sid="pending", enabled=MIC_CAPTURE, echo_ws=MIC_ECHO_WS)

    try:
        _admin_emit and _admin_emit(
            "ws_handshake_enter",
            path=scope.get("path"),
            raw_query=(scope.get("query_string") or b"").decode("utf-8", "ignore"),
        )
    except Exception:
        pass

    if scope.get("type") != "websocket":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})
        return

    sid = _get_session_id(scope)
    _jlog("mic_capture_cfg", sid=sid, enabled=MIC_CAPTURE, echo_ws=MIC_ECHO_WS)

    # Auth
    require_token = os.getenv("WS_TOKEN_REQUIRED", "1").lower() not in ("0", "false", "no")
    bearer_only = os.getenv("WS_BEARER_ONLY", "1").lower() not in ("0", "false", "no")
    fail_limit = int(os.getenv("WS_FAIL_LIMIT", "10"))
    fail_window_sec = float(os.getenv("WS_FAIL_WINDOW_SEC", "60"))
    client_ip = _client_ip_from_scope(scope)

    token = None
    try:
        for _sp in (scope.get("subprotocols") or []):
            if isinstance(_sp, str) and _sp.startswith("bearer."):
                token = _sp.split(".", 1)[1].strip()
                break
    except Exception:
        pass
    if not token:
        try:
            hdrs = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
            if "authorization" in hdrs and hdrs["authorization"].lower().startswith("bearer "):
                token = hdrs["authorization"].split(" ", 1)[1].strip()
        except Exception:
            pass
    if (not bearer_only) and (not token) and scope.get("query_string"):
        try:
            q = dict([tuple(p.split("=", 1)) for p in scope.get("query_string").decode().split("&") if "=" in p])
            token = q.get("ws_token") or token
        except Exception:
            pass
    if require_token:
        try:
            _ = verify_ws_token(token or "")
        except Exception:
            over = ws_metrics.record_fail(client_ip, fail_limit, fail_window_sec)
            _jlog("ws_auth_fail", ip=client_ip, sid=sid, over_limit=over, via="preaccept")
            try:
                _admin_emit and _admin_emit("ws_auth_fail", sid=sid, ip=client_ip, over_limit=over)
            except Exception:
                pass
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 4401})
            return

    await send({"type": "websocket.accept", "subprotocol": "bearer"})

    with contextlib.suppress(Exception):
        db.memory.setdefault("greet_turns", {}).pop(sid, None)

    bus_task = asyncio.create_task(_pump_bus_to_client(sid, send))

    async def _ping_loop():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await _ws_send_json(send, {"type": "keepalive", "ts": int(time.time() * 1000)})
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    ping_task = asyncio.create_task(_ping_loop())

    try:
        await _ws_send_json(send, {"type": "ready", "session_id": sid})
    except Exception:
        with contextlib.suppress(Exception):
            await send({"type": "websocket.close", "code": 1011, "reason": "initial_ready_failed"})
        return

    cfg: Dict[str, Any] = {}
    buf = TurnBuffer()
    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None
    dg_connect_task: Optional[asyncio.Task] = None
    dg_state: str = "closed"
    turn_id_ref = [0]
    final_seen = [False]

    # Turn-scoped buffering + state
    buffered_chunks: Deque[bytes] = deque()
    sent_any_audio = [False]
    asr_ready_evt: asyncio.Event = asyncio.Event()
    asr_ready_wait_s: float = float(os.getenv("ASR_READY_WAIT_S", "3.0"))
    max_buffered_chunks = max(1, int(os.getenv("ASR_MAX_BUFFERED_CHUNKS", "16")))
    turn_connect_started = [False]

    # NEW: per-turn mic capture state
    mic_chunks: List[bytes] = []
    mic_first_ts = [0.0]
    mic_last_ts = [0.0]

    async def _ensure_dg_connected() -> bool:
        nonlocal dg, rx_task, dg_connect_task, dg_state

        if not _has_deepgram_key():
            return False

        if dg_state == "open" and dg is not None:
            return True

        if dg_state == "connecting" and dg_connect_task is not None:
            with contextlib.suppress(Exception):
                await dg_connect_task
            return dg_state == "open" and dg is not None

        connect_result = {"ok": False}

        async def _connect() -> None:
            nonlocal dg, rx_task, dg_connect_task, dg_state, connect_result
            try:
                dg_state = "connecting"
                with contextlib.suppress(Exception):
                    asr_ready_evt.clear()
                cfg['_transport'] = transport
                cfg['_jlog'] = _jlog
                _jlog("asr_connect_begin", sid=sid, transport=transport)
                client = DeepgramClient(cfg)
                dg = client
                await client.connect()
                dg_state = "open"
                connect_result["ok"] = True
                turn_id_ref[0] = buf.turn_seq + 1
                rx_task = asyncio.create_task(
                    _pump_dg_to_client(client, send, turn_id_ref, final_seen, sid, asr_ready_evt, _flush_buffered_chunks)
                )
                _jlog("asr_connect_ok", sid=sid)
            except Exception as e:
                dg_state = "closed"
                dg = None
                _jlog("asr_connect_fail", sid=sid, err=type(e).__name__)
                with contextlib.suppress(Exception):
                    await _ws_send_json(send, make_error("asr_connect_fail", type(e).__name__))
                with contextlib.suppress(Exception):
                    _admin_emit and _admin_emit("asr:error", session_id=sid, error=f"connect:{type(e).__name__}")
            finally:
                dg_connect_task = None

        dg_connect_task = asyncio.create_task(_connect())
        with contextlib.suppress(Exception):
            await dg_connect_task
        return connect_result["ok"]

    async def _send_chunk(data: bytes, *, from_buffer: bool = False, retry: bool = True) -> bool:
        nonlocal dg, dg_state
        if dg is None:
            return False
        try:
            await dg.send(data)
            sent_any_audio[0] = True
            _jlog("ws_audio_forward", sid=sid, bytes=len(data), buffered=from_buffer)
            return True
        except RuntimeError as e:
            if "deepgram_not_connected" in str(e).lower() and retry:
                _jlog("asr_send_retry", sid=sid)
                if not asr_ready_evt.is_set():
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(asr_ready_evt.wait(), timeout=1.0)
                else:
                    await asyncio.sleep(0.05)
                return await _send_chunk(data, from_buffer=from_buffer, retry=False)
            _jlog("asr_send_error", sid=sid, err=type(e).__name__)
        except Exception as e:
            _jlog("asr_send_error", sid=sid, err=type(e).__name__)
        return False

    async def _flush_buffered_chunks() -> None:
        nonlocal dg
        if not buffered_chunks:
            return
        if not _has_deepgram_key() or dg is None:
            return
        while buffered_chunks:
            chunk = buffered_chunks[0]
            ok = await _send_chunk(chunk, from_buffer=True)
            if ok:
                buffered_chunks.popleft()
                continue
            break

    try:
        while True:
            ev = await receive()
            et = ev.get("type")

            if et == "websocket.disconnect":
                break

            if et == "websocket.receive":
                # -------------------- Binary / audio lane --------------------
                if ev.get("bytes") is not None:
                    now = time.time()
                    chunk = ev.get("bytes") or b""
                    if chunk:
                        _jlog("ws_audio_chunk", sid=sid, bytes=len(chunk))
                        if not audio_sig_logged:
                            with contextlib.suppress(Exception):
                                _jlog("audio_sig", sid=sid, first8_hex=chunk[:8].hex())
                            audio_sig_logged = True

                    if buf.is_empty():
                        # New audio turn
                        turn_id_ref[0] = buf.turn_seq + 1
                        final_seen[0] = False
                        sent_any_audio[0] = False
                        buffered_chunks.clear()
                        turn_connect_started[0] = False
                        # reset mic capture
                        mic_chunks.clear()
                        mic_first_ts[0] = now
                        mic_last_ts[0] = now

                    buf.append(chunk)

                    # capture bytes for diagnostic playback
                    if MIC_CAPTURE:
                        mic_chunks.append(chunk)
                        mic_last_ts[0] = now
                    _jlog("mic_capture_append", sid=sid, turn_id=turn_id_ref[0], chunks=len(mic_chunks), last_bytes=len(chunk))


                    # Detect container early
                    try:
                        if transport.get("container") is None and chunk:
                            det = sniffer.feed(chunk)
                            if det:
                                transport["container"] = getattr(det, "container", None)
                                transport["codec"] = getattr(det, "codec", None)
                                transport["containerized_opus"] = bool(getattr(det, "codec", "") == "opus")
                                _jlog("sniffer_detect",
                                      sid=sid,
                                      container=transport.get("container"),
                                      codec=transport.get("codec"),
                                      containerized_opus=transport.get("containerized_opus"))
                            else:
                                meta = coerce_detection_from_meta(getattr(sniffer, "meta", lambda: None)())
                                if meta and meta.get("container"):
                                    transport["container"] = meta["container"]
                                    transport["codec"] = meta.get("codec")
                                    transport["containerized_opus"] = (meta.get("codec") == "opus")
                                    _jlog("sniffer_detect",
                                          sid=sid,
                                          container=transport.get("container"),
                                          codec=transport.get("codec"),
                                          containerized_opus=transport.get("containerized_opus"))
                    except Exception:
                        pass

                    if not _has_deepgram_key():
                        _jlog("ws_audio_no_key", sid=sid, bytes=len(chunk))
                        continue

                    # Stage early frames
                    if chunk:
                        buffered_chunks.append(chunk)

                    # Ensure provider connection
                    if not turn_connect_started[0]:
                        turn_connect_started[0] = True
                        connected = await _ensure_dg_connected()
                        if not connected:
                            turn_connect_started[0] = False
                    elif dg_state == "connecting" and dg_connect_task is not None:
                        with contextlib.suppress(Exception):
                            await dg_connect_task

                    # Flush when ready
                    if dg is not None:
                        if not asr_ready_evt.is_set():
                            try:
                                await asyncio.wait_for(asr_ready_evt.wait(), timeout=asr_ready_wait_s)
                            except asyncio.TimeoutError:
                                _jlog("asr_not_ready_timeout", sid=sid)
                        if len(buffered_chunks) >= max_buffered_chunks:
                            await _flush_buffered_chunks()
                        await _flush_buffered_chunks()
                    else:
                        _jlog("ws_audio_provider_connecting", sid=sid, queued=len(buffered_chunks))
                    continue

                # -------------------- Text / control lane --------------------
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")

                        if t == "KeepAlive":
                            await _ws_send_json(send, make_keepalive_ack())

                        elif t == "greet":
                            _jlog("ws_greet_recv", sid=sid)
                            async def _bg():
                                try:
                                    from app.services.streaming import run_ws_greet
                                    tid = await asyncio.to_thread(run_ws_greet, sid)
                                    with contextlib.suppress(Exception):
                                        if _admin_emit:
                                            cfg_now = db.get_config()
                                            audio_on = bool((cfg_now or {}).get("feature_audio", True))
                                            _admin_emit("greet:resp", label="greet:resp",
                                                        session_id=sid, turn_id=tid, audio_scheduled=audio_on)
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(send, make_error("greet_fail", e.__class__.__name__))
                            asyncio.create_task(_bg())

                        elif t == "Configure":
                            cfg.update(obj or {})
                            if obj.get("reset"):
                                with contextlib.suppress(Exception):
                                    db.memory.setdefault("greet_turns", {}).pop(sid, None)
                                with contextlib.suppress(Exception):
                                    _admin_emit and _admin_emit("greet:reset", route="/ws/v1/chat",
                                                                label="greet:reset", session_id=sid)
                            if obj.get("greet"):
                                _jlog("ws_greet_recv", sid=sid, via="Configure")
                                async def _bg2():
                                    try:
                                        from app.services.streaming import run_ws_greet
                                        tid = await asyncio.to_thread(run_ws_greet, sid)
                                        with contextlib.suppress(Exception):
                                            if _admin_emit:
                                                cfg_now = db.get_config()
                                                audio_on = bool((cfg_now or {}).get("feature_audio", True))
                                                _admin_emit("greet:resp", label="greet:resp",
                                                            session_id=sid, turn_id=tid, audio_scheduled=audio_on)
                                    except Exception as e:
                                        with contextlib.suppress(Exception):
                                            await _ws_send_json(send, make_error("greet_fail", e.__class__.__name__))
                                asyncio.create_task(_bg2())

                        elif t in ("user_msg", "User", "UserText", "UserMessage", "UserUtterance", "UserTextMessage"):
                            text = (obj.get("text") or "").strip()
                            if not text:
                                continue
                            if len(text) > 8000:
                                await _ws_send_json(send, make_error("payload_too_large", "user_text"))
                                continue
                            corr = obj.get("correlation_user_msg_id") or obj.get("userMsgId")
                            _jlog("ws_user_msg_recv", sid=sid, text_len=len(text), corr=bool(corr))

                            async def _bg_user():
                                try:
                                    await asyncio.to_thread(run_ws_user_turn, sid, text, corr)
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(send, make_error("user_fail", e.__class__.__name__))
                            asyncio.create_task(_bg_user())

                       elif t == "CloseStream":
    _jlog("ws_close_stream", sid=sid)
    if buf.is_empty():
        # Empty turn closure; synthesize ids + reset final tracking.
        turn_id_ref[0] = buf.turn_seq + 1
        final_seen[0] = False

    # --- Get a turn_id (with guard logs) ---
    _jlog("before_close_turn", sid=sid, next_turn_id=buf.turn_seq + 1)
    try:
        turn_id, _pcm = buf.close_turn()
    except Exception as e:
        _jlog("close_turn_fail", sid=sid, err=type(e).__name__)
        turn_id = turn_id_ref[0] or (buf.turn_seq + 1)
        _pcm = None
    _jlog("after_close_turn", sid=sid, turn_id=turn_id)
    turn_id_ref[0] = turn_id

    # ---- CAPTURE FIRST: summarize, save /tmp (or $TMPDIR), and optional WS echo ----
    _jlog("mic_capture_block_enter", sid=sid, turn_id=turn_id, mic_chunks=len(mic_chunks))
    if MIC_CAPTURE:
        try:
            raw = b"".join(mic_chunks)
            _jlog(
                "mic_capture_summary",
                sid=sid, turn_id=turn_id, bytes=len(raw), chunks=len(mic_chunks),
                container=transport.get("container"), codec=transport.get("codec"),
                containerized_opus=transport.get("containerized_opus"),
            )
            if raw:
                base_dir = os.getenv("TMPDIR") or "/tmp"
                if transport.get("containerized_opus"):
                    out_path = os.path.join(base_dir, f"mic_{sid}_{turn_id}.webm")
                    mime = "audio/webm"
                    with open(out_path, "wb") as f: f.write(raw)
                    data_to_echo = raw
                else:
                    rate = int(os.getenv("DG_RAW_SAMPLE_RATE", "48000"))
                    ch = int(os.getenv("DG_RAW_CHANNELS", "1"))
                    wav = _wav_with_header(raw, sample_rate=rate, channels=ch, bits_per_sample=16)
                    out_path = os.path.join(base_dir, f"mic_{sid}_{turn_id}.wav")
                    mime = "audio/wav"
                    with open(out_path, "wb") as f: f.write(wav)
                    data_to_echo = wav
                _jlog("mic_capture_saved", sid=sid, turn_id=turn_id, path=out_path, bytes=len(raw), mime=mime)
                if MIC_ECHO_WS:
                    await _ws_send_diagnostic_audio(send, turn_id, mime, data_to_echo)
        except Exception as e:
            _jlog("mic_capture_fail", sid=sid, err=type(e).__name__)

    # ---- THEN finish the ASR turn (unchanged logic) ----
    synthetic_emitted = False
    if _has_deepgram_key() and dg is not None:
        # If we have buffered chunks but ASR not ready yet, give it a moment then flush.
        if buffered_chunks and not asr_ready_evt.is_set():
            if dg_state == "connecting" and dg_connect_task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(dg_connect_task, timeout=asr_ready_wait_s)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asr_ready_evt.wait(), timeout=asr_ready_wait_s)

        # Flush any staged audio first
        await _flush_buffered_chunks()

        if sent_any_audio[0]:
            # Ask provider to finish; if no final came, synthesize empty final.
            with contextlib.suppress(Exception):
                await dg.close(wait_for_final=True)
            dg_state = "closed"
            _relay_task = rx_task
            rx_task = None
            if _relay_task:
                _relay_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await _relay_task
            dg = None
            with contextlib.suppress(Exception):
                asr_ready_evt.clear()
            if not final_seen[0]:
                final_seen[0] = True
                synthetic_emitted = True
                result_payload = make_results(turn_id, transcript="", is_final=True)
                result_payload["type"] = "Results"
                await _ws_send_json(send, result_payload)
                utterance_payload = make_utterance_end(turn_id)
                utterance_payload["type"] = "UtteranceEnd"
                await _ws_send_json(send, utterance_payload)
        else:
            # Nothing actually went to provider; synthesize final locally
            _jlog("ws_close_skip_no_audio", sid=sid)
            if not final_seen[0]:
                final_seen[0] = True
                synthetic_emitted = True
                result_payload = make_results(turn_id, transcript="", is_final=True)
                result_payload["type"] = "Results"
                await _ws_send_json(send, result_payload)
                utterance_payload = make_utterance_end(turn_id)
                utterance_payload["type"] = "UtteranceEnd"
                await _ws_send_json(send, utterance_payload)
    else:
        # No provider configured: still emit empty final + end to advance the dialog.
        if not final_seen[0]:
            final_seen[0] = True
            synthetic_emitted = True
            await _ws_send_json(send, make_results(turn_id, transcript="", is_final=True))
            await _ws_send_json(send, make_utterance_end(turn_id))

    if synthetic_emitted:
        # Reset so the next turn starts fresh even if no audio chunk arrives.
        final_seen[0] = False

                            # ---- NEW: mic-capture summary, save to /tmp (or $TMPDIR), and optional WS echo ----

                            _jlog("mic_capture_block_enter", sid=sid, turn_id=turn_id, mic_chunks=len(mic_chunks))
                            if MIC_CAPTURE:
                                try:
                                    raw = b"".join(mic_chunks)
                                    _jlog(
                                        "mic_capture_summary",
                                         sid=sid,
                                         turn_id=turn_id,
                                         bytes=len(raw),
                                         chunks=len(mic_chunks),
                                         container=transport.get("container"),
                                         codec=transport.get("codec"),
                                         containerized_opus=transport.get("containerized_opus"),
                                    )

                                    if raw:
                                        base_dir = os.getenv("TMPDIR") or "/tmp"
                                        if transport.get("containerized_opus"):
                                            # Containerized Opus → save WebM bytes as-is
                                            out_path = os.path.join(base_dir, f"mic_{sid}_{turn_id}.webm")
                                            mime = "audio/webm"
                                            with open(out_path, "wb") as f:
                                                f.write(raw)
                                            data_to_echo = raw
                                        else:
                                            # Raw PCM → wrap in a WAV header for easy playback
                                            rate = int(os.getenv("DG_RAW_SAMPLE_RATE", "48000"))
                                            ch = int(os.getenv("DG_RAW_CHANNELS", "1"))
                                            wav = _wav_with_header(raw, sample_rate=rate, channels=ch, bits_per_sample=16)
                                            out_path = os.path.join(base_dir, f"mic_{sid}_{turn_id}.wav")
                                            mime = "audio/wav"
                                            with open(out_path, "wb") as f:
                                                f.write(wav)
                                            data_to_echo = wav

                                        _jlog("mic_capture_saved", sid=sid, turn_id=turn_id, path=out_path, bytes=len(raw), mime=mime)

                                        if MIC_ECHO_WS:
                                            # Send the audio back over WS so the client can play it
                                            await _ws_send_diagnostic_audio(send, turn_id, mime, data_to_echo)
                                except Exception as e:
                                    _jlog("mic_capture_fail", sid=sid, err=type(e).__name__)

                        else:
                            # Unknown type already filtered by schema; no-op to future-proof.
                            pass

                    except ValueError as e:
                        await _ws_send_json(send, make_error("bad_message", str(e)))
                else:
                    # websocket.receive without text/bytes
                    pass
            else:
                # Other ASGI events are ignored
                pass

    finally:
        # Clean up safely; never raise in cleanup
        with contextlib.suppress(Exception):
            if rx_task:
                rx_task.cancel()
                await rx_task
        with contextlib.suppress(Exception):
            if dg is not None:
                await dg.close(wait_for_final=False)
        with contextlib.suppress(Exception):
            bus_task.cancel()
            await bus_task
        with contextlib.suppress(Exception):
            ping_task.cancel()
            await ping_task
        with contextlib.suppress(Exception):
            await send({"type": "websocket.close", "code": 1000, "reason": "normal_shutdown"})


# --- Compatibility wrapper (not used by Starlette mount, kept for tests) ---
try:
    from starlette.websockets import WebSocket as _StarletteWebSocket  # noqa
except Exception:
    _StarletteWebSocket = None


async def ws_chat(websocket):
    """Accept, validate, send ready, then pump frames to keep the connection alive."""
    _jlog("ws_chat_compat_invoked")
    await websocket.accept()
    try:
        sid = _get_session_id(websocket.scope)
    except Exception:
        sid = "default"

    try:
        await websocket.send_text(_dumps({"type": "ready", "session_id": sid}))
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="initial_ready_failed")
            await asyncio.sleep(0.05)
        return

    try:
        await _pump_bus_to_client(sid, lambda msg: websocket.send_text(msg.get("text") or ""))
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            await websocket.close(code=1000, reason="normal_shutdown")
            await asyncio.sleep(0.05)
