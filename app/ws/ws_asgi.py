# app/ws/ws_asgi.py — Phase 2+ (Deepgram wired; WS protocol + delegation; WS-only greet + typed turns)
from __future__ import annotations
import asyncio, os, contextlib, time
from typing import Optional, Dict, Any, Deque
from collections import deque

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
                # If client send fails, yield so cancellation can propagate
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
                continue

            if et in ("user_partial", "user_final"):
                is_final = (et == "user_final")
                text = (ev.get("text") or "").strip()
                _jlog(
                    "dg_transcript",
                    sid=sid,
                    turn_id=turn_id_ref[0],
                    is_final=is_final,
                    chars=len(text),
                    preview=_clip_text(text),
                )
                try:
                    if et == "user_partial":
                        _admin_emit and _admin_emit("asr:first_partial", session_id=sid)
                except Exception:
                    pass

                # Stream ASR result to client (optional UI)
                await send({
                    "type": "websocket.send",
                    "text": _dumps(make_results(turn_id_ref[0], transcript=text, confidence=0.0, is_final=is_final)),
                })

                if is_final:
                    final_seen[0] = True
                    # Let client know the utterance is closed
                    await send({"type": "websocket.send", "text": _dumps(make_utterance_end(turn_id_ref[0]))})
                    try:
                        _admin_emit and _admin_emit("asr:final", session_id=sid)
                    except Exception:
                        pass

                    # NEW: Kick the LLM/Chip turn on final transcript
                    if text:
                        async def _bg_turn():
                            try:
                                # Offload to thread (streaming pipeline is sync)
                                await asyncio.to_thread(run_ws_user_turn, sid, text, None)
                            except Exception as e:
                                try:
                                    await send({
                                        "type": "websocket.send",
                                        "text": _dumps(make_error("llm_turn_fail", e.__class__.__name__)),
                                    })
                                except Exception:
                                    pass
                        asyncio.create_task(_bg_turn())

            elif et == "asr_error":
                _jlog(
                    "dg_asr_error",
                    sid=sid,
                    turn_id=turn_id_ref[0],
                    error=_clip_text(str(ev.get("error") or "unknown"), 160),
                )
                try:
                    _admin_emit and _admin_emit("asr:error", session_id=sid, error=str(ev.get("error") or "unknown"))
                except Exception:
                    pass
                await send({
                    "type": "websocket.send",
                    "text": _dumps(make_error("asr_error", str(ev.get("error") or "unknown"))),
                })
    except asyncio.CancelledError:
        return
    except Exception as e:
        try:
            await send({"type": "websocket.send", "text": _dumps(make_error("relay_fail", e.__class__.__name__))})
        except Exception:
            pass


async def _ws_chat_asgi_impl(scope, receive, send):
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
            q = dict(
                [
                    tuple(p.split("=", 1))
                    for p in scope.get("query_string").decode().split("&")
                    if "=" in p
                ]
            )
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
            try:
                await send({"type": "websocket.close", "code": 4401})
            except Exception:
                pass
            return

    await send({"type": "websocket.accept", "subprotocol": "bearer"})

    try:
        db.memory.setdefault("greet_turns", {}).pop(sid, None)
    except Exception:
        pass

    bus_task = asyncio.create_task(_pump_bus_to_client(sid, send))

    async def _ping_loop():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await send({
                        "type": "websocket.send",
                        "text": _dumps({"type": "keepalive", "ts": int(time.time() * 1000)}),
                    })
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    ping_task = asyncio.create_task(_ping_loop())

    try:
        await send({"type": "websocket.send", "text": _dumps({"type": "ready", "session_id": sid})})
    except Exception:
        try:
            await send({"type": "websocket.close", "code": 1011, "reason": "initial_ready_failed"})
        except Exception:
            pass
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
    asr_ready_wait_s: float = float(os.getenv("ASR_READY_WAIT_S", "1.5"))
    max_buffered_chunks = max(1, int(os.getenv("ASR_MAX_BUFFERED_CHUNKS", "16")))
    turn_connect_started = [False]

    async def _ensure_dg_connected() -> bool:
        """Connect to ASR provider once per session; never tear down the WS on provider failures."""
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
                try:
                    if asr_ready_evt.is_set():
                        asr_ready_evt.clear()
                except Exception:
                    pass
                client = DeepgramClient(cfg)
                dg = client
                await client.connect()
                dg_state = "open"
                connect_result["ok"] = True
                turn_id_ref[0] = buf.turn_seq + 1
                rx_task = asyncio.create_task(
                    _pump_dg_to_client(client, send, turn_id_ref, final_seen, sid, asr_ready_evt)
                )
                _jlog("asr_connect_ok", sid=sid)
            except Exception as e:
                dg_state = "closed"
                dg = None
                _jlog("asr_connect_fail", sid=sid, err=type(e).__name__)
                try:
                    await send({
                        "type": "websocket.send",
                        "text": _dumps(make_error("asr_connect_fail", type(e).__name__)),
                    })
                except Exception:
                    pass
                try:
                    _admin_emit and _admin_emit("asr:error", session_id=sid, error=f"connect:{type(e).__name__}")
                except Exception:
                    pass
            finally:
                dg_connect_task = None

        dg_connect_task = asyncio.create_task(_connect())
        with contextlib.suppress(Exception):
            await dg_connect_task
        return connect_result["ok"]

    async def _send_chunk(data: bytes, *, from_buffer: bool = False, retry: bool = True) -> bool:
        """Send audio to Deepgram, retrying once on connection race."""
        nonlocal dg, dg_state
        if dg is None:
            return False

        try:
            await dg.send(data)
            sent_any_audio[0] = True
            _jlog(
                "ws_audio_forward",
                sid=sid,
                bytes=len(data),
                buffered=from_buffer,
            )
            return True

        except RuntimeError as e:
            if "deepgram_not_connected" in str(e).lower() and retry:
                dg_state = "closed"
                _jlog("asr_send_retry", sid=sid)
                await _ensure_dg_connected()
                if not asr_ready_evt.is_set():
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(asr_ready_evt.wait(), timeout=0.25)
                return await _send_chunk(data, from_buffer=from_buffer, retry=False)
            _jlog("asr_send_error", sid=sid, err=type(e).__name__)
        except Exception as e:
            _jlog("asr_send_error", sid=sid, err=type(e).__name__)
        return False

    async def _flush_buffered_chunks() -> None:
        """Flush buffered audio once Deepgram is ready."""
        nonlocal dg
        if not buffered_chunks:
            return
        if not _has_deepgram_key() or dg is None:
            buffered_chunks.clear()
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
                # Binary/audio lane
                if ev.get("bytes") is not None:
                    chunk = ev.get("bytes") or b""
                    if chunk:
                        _jlog("ws_audio_chunk", sid=sid, bytes=len(chunk))
                    if buf.is_empty():
                        # New audio turn; prime turn id + reset final tracking.
                        turn_id_ref[0] = buf.turn_seq + 1
                        final_seen[0] = False
                        sent_any_audio[0] = False
                        buffered_chunks.clear()
                        turn_connect_started[0] = False
                    buf.append(chunk)
                    if _has_deepgram_key():
                        # Ensure provider is connected once per turn; await in-flight connects.
                        if not turn_connect_started[0]:
                            turn_connect_started[0] = True
                            connected = await _ensure_dg_connected()
                            if not connected:
                                turn_connect_started[0] = False
                        elif dg_state == "connecting" and dg_connect_task is not None:
                            with contextlib.suppress(Exception):
                                await dg_connect_task

                        if dg is not None:
                            # Always stage in the buffer first (avoids losing early chunks)
                            buffered_chunks.append(chunk)

                            # If buffer is getting large, try to flush opportunistically
                            if len(buffered_chunks) >= max_buffered_chunks:
                                await _flush_buffered_chunks()

                            # If provider hasn't signaled open yet, wait briefly (first-chunk race guard)
                            if not asr_ready_evt.is_set():
                                try:
                                    await asyncio.wait_for(asr_ready_evt.wait(), timeout=asr_ready_wait_s)
                                except asyncio.TimeoutError:
                                    _jlog("asr_not_ready_timeout", sid=sid)

                            # Final attempt to flush staged audio
                            await _flush_buffered_chunks()
                        else:
                            _jlog("ws_audio_no_provider", sid=sid, bytes=len(chunk))
                    else:
                        _jlog("ws_audio_no_key", sid=sid, bytes=len(chunk))
                    continue

                # Text/control lane
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")

                        if t == "KeepAlive":
                            await send({"type": "websocket.send", "text": _dumps(make_keepalive_ack())})

                        # OPTIONAL WS greet alias: allow {type:"greet"} in addition to Configure{greet:true}
                        elif t == "greet":
                            _jlog("ws_greet_recv", sid=sid)
                            async def _bg():
                                try:
                                    from app.services.streaming import run_ws_greet
                                    tid = await asyncio.to_thread(run_ws_greet, sid)
                                    try:
                                        if _admin_emit:
                                            cfg_now = db.get_config()
                                            audio_on = bool((cfg_now or {}).get("feature_audio", True))
                                            _admin_emit(
                                                "greet:resp",
                                                label="greet:resp",
                                                session_id=sid,
                                                turn_id=tid,
                                                audio_scheduled=audio_on,
                                            )
                                    except Exception:
                                        pass
                                except Exception as e:
                                    try:
                                        await send({
                                            "type": "websocket.send",
                                            "text": _dumps(make_error("greet_fail", e.__class__.__name__)),
                                        })
                                    except Exception:
                                        pass
                            asyncio.create_task(_bg())

                        elif t == "Configure":
                            cfg.update(obj or {})

                            if obj.get("reset"):
                                try:
                                    db.memory.setdefault("greet_turns", {}).pop(sid, None)
                                except Exception:
                                    pass
                                try:
                                    _admin_emit and _admin_emit(
                                        "greet:reset", route="/ws/v1/chat", label="greet:reset", session_id=sid
                                    )
                                except Exception:
                                    pass

                            if obj.get("greet"):
                                _jlog("ws_greet_recv", sid=sid, via="Configure")
                                async def _bg():
                                    try:
                                        from app.services.streaming import run_ws_greet
                                        tid = await asyncio.to_thread(run_ws_greet, sid)
                                        try:
                                            if _admin_emit:
                                                cfg_now = db.get_config()
                                                audio_on = bool((cfg_now or {}).get("feature_audio", True))
                                                _admin_emit(
                                                    "greet:resp",
                                                    label="greet:resp",
                                                    session_id=sid,
                                                    turn_id=tid,
                                                    audio_scheduled=audio_on,
                                                )
                                        except Exception:
                                            pass
                                    except Exception as e:
                                        try:
                                            await send({
                                                "type": "websocket.send",
                                                "text": _dumps(make_error("greet_fail", e.__class__.__name__)),
                                            })
                                        except Exception:
                                            pass
                                asyncio.create_task(_bg())

                        # TEXT turns (support new WS-only + legacy aliases)
                        elif t in ("user_msg", "User", "UserText", "UserMessage", "UserUtterance", "UserTextMessage"):
                            text = (obj.get("text") or "").strip()
                            if not text:
                                continue
                            if len(text) > 8000:
                                await send({
                                    "type": "websocket.send",
                                    "text": _dumps(make_error("payload_too_large", "user_text")),
                                })
                                continue

                            # Accept both new and legacy correlation keys
                            corr = obj.get("correlation_user_msg_id") or obj.get("userMsgId")
                            _jlog("ws_user_msg_recv", sid=sid, text_len=len(text), corr=bool(corr))

                            async def _bg_user():
                                try:
                                    await asyncio.to_thread(run_ws_user_turn, sid, text, corr)
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await send({
                                            "type": "websocket.send",
                                            "text": _dumps(make_error("user_fail", e.__class__.__name__)),
                                        })
                            asyncio.create_task(_bg_user())

                        elif t == "CloseStream":
                            _jlog("ws_close_stream", sid=sid)
                            if buf.is_empty():
                                # Empty turn closure; synthesize ids + reset final tracking.
                                turn_id_ref[0] = buf.turn_seq + 1
                                final_seen[0] = False
                            turn_id, _pcm = buf.close_turn()
                            turn_id_ref[0] = turn_id
                            synthetic_emitted = False
                            if _has_deepgram_key() and dg is not None:
                                # *** GRACE: if we have buffered chunks but ASR not ready yet, give it 500ms then flush.
                                if buffered_chunks and not asr_ready_evt.is_set():
                                    with contextlib.suppress(asyncio.TimeoutError):
                                        await asyncio.wait_for(asr_ready_evt.wait(), timeout=0.5)

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
                                    try:
                                        if asr_ready_evt and asr_ready_evt.is_set():
                                            asr_ready_evt.clear()
                                    except Exception:
                                        pass
                                    if not final_seen[0]:
                                        final_seen[0] = True
                                        synthetic_emitted = True
                                        result_payload = make_results(turn_id, transcript="", is_final=True)
                                        # Keep schema compatibility for clients looking at "type"
                                        RESULTS_TYPE = "Results"
                                        result_payload["type"] = RESULTS_TYPE
                                        await send({"type": "websocket.send", "text": _dumps(result_payload)})
                                        UTTERANCE_END_TYPE = "UtteranceEnd"
                                        utterance_payload = make_utterance_end(turn_id)
                                        utterance_payload["type"] = UTTERANCE_END_TYPE
                                        await send({"type": "websocket.send", "text": _dumps(utterance_payload)})
                                else:
                                    # Nothing actually went to provider; skip provider close, synthesize final locally
                                    _jlog("ws_close_skip_no_audio", sid=sid)
                                    if not final_seen[0]:
                                        final_seen[0] = True
                                        synthetic_emitted = True
                                        RESULTS_TYPE = "Results"
                                        UTTERANCE_END_TYPE = "UtteranceEnd"
                                        result_payload = make_results(turn_id, transcript="", is_final=True)
                                        result_payload["type"] = RESULTS_TYPE
                                        await send({"type": "websocket.send", "text": _dumps(result_payload)})
                                        utterance_payload = make_utterance_end(turn_id)
                                        utterance_payload["type"] = UTTERANCE_END_TYPE
                                        await send({"type": "websocket.send", "text": _dumps(utterance_payload)})
                            else:
                                # No provider configured: still emit empty final + end to advance the dialog.
                                if not final_seen[0]:
                                    final_seen[0] = True
                                    synthetic_emitted = True
                                    await send({
                                        "type": "websocket.send",
                                        "text": _dumps(make_results(turn_id, transcript="", is_final=True)),
                                    })
                                    await send({"type": "websocket.send", "text": _dumps(make_utterance_end(turn_id))})
                            if synthetic_emitted:
                                # Reset so the next turn starts fresh even if no audio chunk arrives.
                                final_seen[0] = False
                        else:
                            # Unknown type already filtered by schema; no-op to future-proof.
                            pass
                    except ValueError as e:
                        await send({"type": "websocket.send", "text": _dumps(make_error("bad_message", str(e)))})
            else:
                # Other ASGI events are ignored
                pass

    finally:
        # Tear down in a safe order; never raise in cleanup
        try:
            if rx_task:
                rx_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await rx_task
        except Exception:
            pass
        try:
            if dg is not None:
                with contextlib.suppress(Exception):
                    await dg.close(wait_for_final=False)
                dg_state = "closed"
        except Exception:
            pass
        try:
            bus_task.cancel()
            with contextlib.suppress(Exception):
                await bus_task
        except Exception:
            pass
        try:
            ping_task.cancel()
            with contextlib.suppress(Exception):
                await ping_task
        except Exception:
            pass
        try:
            await send({"type": "websocket.close", "code": 1000, "reason": "normal_shutdown"})
        except Exception:
            pass


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
        try:
            await websocket.close(code=1011, reason="initial_ready_failed")
            await asyncio.sleep(0.05)
        finally:
            return

    try:
        await _pump_bus_to_client(sid, lambda msg: websocket.send_text(msg.get("text") or ""))
    except Exception:
        pass
    finally:
        try:
            await websocket.close(code=1000, reason="normal_shutdown")
            await asyncio.sleep(0.05)
        except Exception:
            pass
