# app/ws/ws_asgi.py — Phase 2+ (Deepgram wired; WS protocol + delegation; WS-only greet + typed turns)
from __future__ import annotations
import asyncio, os, contextlib, time, io, struct, base64, uuid, copy
from typing import Optional, Dict, Any, Deque, Callable, Awaitable, List, Tuple, Set
from collections import deque
from app.services.audio.container_sniffer import (
    AudioContainerSniffer,
    coerce_detection_from_meta,
)
from app.services.audio.raw_fallback import (
    DetectionSignal as RawDetectionSignal,
    StreamMeta as RawStreamMeta,
    StreamStats as RawStreamStats,
    raw_fallback_disabled,
    coerce_to_raw_config,
    normalize_pcm_frame,
    should_use_raw_fallback,
)

from .schema_v1 import (
    parse_client_json,
    make_keepalive_ack,
    make_results,
    make_utterance_end,
    make_error,
)
from .turn_buffer import TurnBuffer
from app.services.streaming_asr.deepgram_client import (
    DeepgramClient,
    DeepgramDrainTimeoutError,
)
from app.security.ws_token import verify as verify_ws_token
from app.db import db
from app.services.greet_idempotency import clear_greet_turn_cache
from app.metrics import ws_metrics

# NEW: invoke LLM on final transcript
from app.services.streaming import run_ws_user_turn, prepare_turn_metadata  # NEW
from app.nlu.universal_interpreter import ensure_all_fields as _ensure_universal_fields
from app.ws.barge import BargeState
from app.ws.bus import bus
from app.policy import nudges
from app.session_state import (
    note_partial,
    note_utterance_end,
    set_asr_stream_open,
    set_phase,
    set_recorder_active,
)

# Optional admin emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    _admin_emit = None


ACTIVE_WS: dict[str, dict[str, Any]] = {}
ACTIVE_WS_LOCK = asyncio.Lock()

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


def _emit_admin_nlu_event(text: str,
                          sid: str,
                          *,
                          dialog_nlu: Optional[Dict[str, Any]] = None,
                          universal: Optional[Dict[str, Any]] = None) -> None:
    if not text:
        return
    admin_cb = globals().get("_admin_emit")
    if not callable(admin_cb):
        return

    meta_stub = {"source": "user_ws", "channel": "ws"}
    prepared_meta: Dict[str, Any] = {}
    computed_dialog: Dict[str, Any] = {}
    if isinstance(dialog_nlu, dict):
        computed_dialog = dict(dialog_nlu)
    try:
        if not computed_dialog:
            prepared_meta, dialog_nlu_raw, _ = prepare_turn_metadata(text, dict(meta_stub))
            if isinstance(dialog_nlu_raw, dict):
                computed_dialog = dict(dialog_nlu_raw)
        if universal is None:
            if not prepared_meta:
                prepared_meta, _, _ = prepare_turn_metadata(text, dict(meta_stub))
            raw_universal = {}
            if isinstance(prepared_meta, dict):
                raw_universal = prepared_meta.get("universal") or {}
            if isinstance(raw_universal, dict):
                universal = _ensure_universal_fields(raw_universal)
    except Exception:
        if not isinstance(computed_dialog, dict):
            computed_dialog = {}
        if universal is None:
            universal = None

    dialog_nlu = computed_dialog if isinstance(computed_dialog, dict) else {}
    if universal is not None and not isinstance(universal, dict):
        universal = None

    def _safe_copy(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    slots_dict: Dict[str, Any] = {}
    extra_slots = dialog_nlu.get("slots")
    if isinstance(extra_slots, dict):
        try:
            slots_dict.update(_safe_copy(extra_slots))
        except Exception:
            slots_dict.update(extra_slots)
    entities = dialog_nlu.get("entities")
    if isinstance(entities, dict):
        slots_dict.setdefault("entities", _safe_copy(entities))
    elif isinstance(entities, list):
        slots_dict.setdefault("entities", _safe_copy(entities))
    products = dialog_nlu.get("products")
    if isinstance(products, list):
        slots_dict.setdefault("products", list(products))

    payload = {
        "event": "nlu",
        "intent": dialog_nlu.get("intent"),
        "confidence": dialog_nlu.get("confidence"),
        "slots": slots_dict,
        "text": text,
        "session_id": sid,
    }
    if isinstance(universal, dict):
        payload["universal"] = _safe_copy(universal)

    try:
        admin_cb("nlu", **payload)
    except Exception:
        pass


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


def _wav_with_header(
    pcm: bytes, sample_rate: int, channels: int, bits_per_sample: int = 16
) -> bytes:
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
    buf.write(
        struct.pack(
            "<IHHIIHH",
            16,
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
    )
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
    await _ws_send_json(
        send,
        {
            "type": "diagnostic_audio",
            "turn_id": str(turn_id),
            "mime": mime,
            "total_bytes": total,
            "part": part,
            "is_last": (total == 0),
            "b64": "",  # header-only announcement
        },
    )
    while off < total:
        chunk = data[off : off + CHUNK]
        off += len(chunk)
        part += 1
        await _ws_send_json(
            send,
            {
                "type": "diagnostic_audio",
                "turn_id": str(turn_id),
                "mime": mime,
                "total_bytes": total,
                "part": part,
                "is_last": (off >= total),
                "b64": base64.b64encode(chunk).decode("ascii"),
            },
        )


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
                await send(
                    {
                        "type": "websocket.send",
                        "text": _json.dumps(
                            fr, separators=(",", ":"), ensure_ascii=False
                        ),
                    }
                )
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
    pending_final_turns,
    synthetic_final_turns,
    completed_llm_turns,
    sid: str,
    asr_ready_evt: Optional[asyncio.Event] = None,
    on_asr_open_flush: Optional[Callable[[], Awaitable[None]]] = None,
    stream_stats: Optional[RawStreamStats] = None,
    turn_timing: Optional[Dict[str, List[float]]] = None,
    on_turn_finish: Optional[Callable[[int, str, bool, int], None]] = None,
):
    """Relay Deepgram events to client and, on final, kick LLM turn."""
    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                delta_ms = None
                now_ts = time.time()
                if turn_timing is not None:
                    try:
                        holder = turn_timing.setdefault("dg_open", [0.0])
                        holder[0] = now_ts
                        start_ts = turn_timing.get("start", [0.0])[0]
                        if start_ts:
                            delta_ms = int((now_ts - start_ts) * 1000)
                    except Exception:
                        pass
                _jlog(
                    "dg_asr_open",
                    sid=sid,
                    turn_id=turn_id_ref[0],
                    delta_from_turn_ms=delta_ms,
                )
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
                        await on_asr_open_flush()
                        await asyncio.sleep(0.05)
                        await on_asr_open_flush()
                except Exception:
                    pass
                continue

            if et in ("user_partial", "user_final"):
                is_final = et == "user_final"
                text = (ev.get("text") or "").strip()
                turn_id_for_event = turn_id_ref[0]
                if is_final:
                    next_turn_id = None
                    try:
                        next_turn_id = pending_final_turns.popleft()
                    except Exception:
                        next_turn_id = None
                    if next_turn_id is None:
                        try:
                            next_turn_id = min(synthetic_final_turns)
                            synthetic_final_turns.discard(next_turn_id)
                        except Exception:
                            next_turn_id = turn_id_ref[0]
                    else:
                        synthetic_final_turns.discard(next_turn_id)
                    turn_id_for_event = next_turn_id
                if not is_final:
                    nudges.cancel_idle_timers(sid, source="asr_partial")
                    try:
                        asr_seen_partial[0] = True
                    except Exception:
                        pass
                    if turn_timing is not None:
                        try:
                            first_holder = turn_timing.setdefault(
                                "first_partial", [0.0]
                            )
                            if not first_holder[0]:
                                first_holder[0] = time.time()
                        except Exception:
                            pass
                    try:
                        note_partial(sid)
                    except Exception:
                        pass
                _jlog(
                    "dg_transcript",
                    sid=sid,
                    turn_id=turn_id_for_event,
                    is_final=is_final,
                    chars=len(text),
                    preview=_clip_text(text),
                )
                try:
                    if et == "user_partial":
                        _admin_emit and _admin_emit("asr:first_partial", session_id=sid)
                except Exception:
                    pass

                await _ws_send_json(
                    send,
                    make_results(
                        turn_id_for_event,
                        transcript=text,
                        confidence=0.0,
                        is_final=is_final,
                    ),
                )

                if is_final:
                    final_seen[0] = True
                    if turn_timing is not None and text:
                        try:
                            first_holder = turn_timing.setdefault(
                                "first_partial", [0.0]
                            )
                            if not first_holder[0]:
                                first_holder[0] = time.time()
                        except Exception:
                            pass
                    if on_turn_finish:
                        try:
                            on_turn_finish(
                                turn_id_for_event, "provider_final", False, len(text)
                            )
                        except Exception:
                            pass
                    await _ws_send_json(send, make_utterance_end(turn_id_for_event))
                    try:
                        note_utterance_end(sid)
                    except Exception:
                        pass
                    try:
                        set_recorder_active(sid, False)
                    except Exception:
                        pass
                    try:
                        _admin_emit and _admin_emit("asr:final", session_id=sid)
                    except Exception:
                        pass

                    if text:
                        dialog_nlu_pre: Dict[str, Any] = {}
                        universal_pre: Dict[str, Any] = {}
                        meta_stub = {"source": "user_ws", "channel": "ws"}
                        try:
                            prepared_meta, dialog_nlu_raw, _ = prepare_turn_metadata(
                                text, dict(meta_stub)
                            )
                            if isinstance(dialog_nlu_raw, dict):
                                dialog_nlu_pre = dict(dialog_nlu_raw)
                            if isinstance(prepared_meta, dict):
                                raw_universal = prepared_meta.get("universal") or {}
                                if isinstance(raw_universal, dict):
                                    universal_pre = _ensure_universal_fields(raw_universal)
                        except Exception:
                            dialog_nlu_pre = {}
                            universal_pre = {}

                        _emit_admin_nlu_event(
                            text,
                            sid,
                            dialog_nlu=dialog_nlu_pre,
                            universal=universal_pre,
                        )

                        meta_overrides: Optional[Dict[str, Any]] = None
                        if dialog_nlu_pre or universal_pre:
                            meta_overrides = {}
                            if dialog_nlu_pre:
                                meta_overrides["nlu"] = dict(dialog_nlu_pre)
                                meta_overrides["dialog_nlu"] = dict(dialog_nlu_pre)
                            if universal_pre:
                                meta_overrides["universal"] = dict(universal_pre)

                        if turn_id_for_event in completed_llm_turns:
                            with contextlib.suppress(Exception):
                                _jlog(
                                    "llm_turn_skip_duplicate",
                                    sid=sid,
                                    turn_id=turn_id_for_event,
                                )
                        else:
                            completed_llm_turns.add(turn_id_for_event)

                            async def _bg_turn(meta_payload=meta_overrides):
                                try:
                                    await asyncio.to_thread(
                                        run_ws_user_turn,
                                        sid,
                                        text,
                                        None,
                                        meta_overrides=meta_payload,
                                    )
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(
                                            send,
                                            make_error(
                                                "llm_turn_fail", e.__class__.__name__
                                            ),
                                        )

                            asyncio.create_task(_bg_turn())

            elif et == "asr_error":
                if stream_stats is not None:
                    try:
                        stream_stats.note_provider_error()
                    except Exception:
                        pass
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
    transport = {
        "protocol": "websocket",
        "container": None,
        "codec": None,
        "containerized_opus": False,
        "features": [],
    }
    sniffer = AudioContainerSniffer()
    stream_meta = RawStreamMeta()
    stream_stats = RawStreamStats(meta=stream_meta)
    fallback_detection: Optional[RawDetectionSignal] = None
    audio_sig_logged = False

    MIC_CAPTURE = _env_truth("MIC_CAPTURE", False)
    MIC_ECHO_WS = _env_truth("MIC_ECHO_WS", False)

    _jlog("mic_capture_cfg", sid="pending", enabled=MIC_CAPTURE, echo_ws=MIC_ECHO_WS)

    raw_query = (scope.get("query_string") or b"").decode("utf-8", "ignore")

    try:
        _admin_emit and _admin_emit(
            "ws_handshake_enter",
            path=scope.get("path"),
            raw_query=raw_query,
        )
    except Exception:
        pass

    if scope.get("type") != "websocket":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})
        return

    sid = _get_session_id(scope)
    conn_id = uuid.uuid4().hex
    start_ts = time.time()
    last_msg_ts = start_ts
    had_disconnect = False
    active_ws_registered = False
    active_ws_closed = False
    _jlog("mic_capture_cfg", sid=sid, enabled=MIC_CAPTURE, echo_ws=MIC_ECHO_WS)

    # Auth
    require_token = os.getenv("WS_TOKEN_REQUIRED", "1").lower() not in (
        "0",
        "false",
        "no",
    )
    bearer_only = os.getenv("WS_BEARER_ONLY", "1").lower() not in ("0", "false", "no")
    fail_limit = int(os.getenv("WS_FAIL_LIMIT", "10"))
    fail_window_sec = float(os.getenv("WS_FAIL_WINDOW_SEC", "60"))
    client_ip = _client_ip_from_scope(scope)

    token = None
    try:
        for _sp in scope.get("subprotocols") or []:
            if isinstance(_sp, str) and _sp.startswith("bearer."):
                token = _sp.split(".", 1)[1].strip()
                break
    except Exception:
        pass
    if not token:
        try:
            hdrs = {
                k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])
            }
            if "authorization" in hdrs and hdrs["authorization"].lower().startswith(
                "bearer "
            ):
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
            _jlog(
                "ws_auth_fail", ip=client_ip, sid=sid, over_limit=over, via="preaccept"
            )
            try:
                _admin_emit and _admin_emit(
                    "ws_auth_fail", sid=sid, ip=client_ip, over_limit=over
                )
            except Exception:
                pass
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 4401})
            return

    async def _register_active_ws_entry() -> Optional[int]:
        nonlocal active_ws_registered
        try:
            async with ACTIVE_WS_LOCK:
                ACTIVE_WS[conn_id] = {
                    "sid": sid,
                    "client_ip": client_ip,
                    "start_ts": start_ts,
                }
                active_count = len(ACTIVE_WS)
            active_ws_registered = True
            return active_count
        except Exception:
            return None

    async def _remove_active_ws_entry(source: str) -> None:
        nonlocal active_ws_closed
        if active_ws_closed:
            return
        active_ws_closed = True
        if not active_ws_registered:
            return
        entry = None
        active_count = None
        try:
            async with ACTIVE_WS_LOCK:
                entry = ACTIVE_WS.pop(conn_id, None)
                active_count = len(ACTIVE_WS)
        except Exception:
            entry = None
            active_count = None
        if entry is None:
            with contextlib.suppress(Exception):
                _jlog("ws_conn_missing", conn_id=conn_id, sid=sid, source=source)
            return
        with contextlib.suppress(Exception):
            _jlog(
                "ws_conn_close",
                conn_id=conn_id,
                sid=sid,
                active=active_count,
                source=source,
            )
        with contextlib.suppress(Exception):
            if _admin_emit:
                _admin_emit("ws_conn_close", conn_id=conn_id, active=active_count)

    await send({"type": "websocket.accept", "subprotocol": "bearer"})

    with contextlib.suppress(Exception):
        _jlog(
            "ws_conn_open",
            sid=sid,
            conn_id=conn_id,
            client_ip=client_ip,
            path=scope.get("path"),
            query_string=raw_query,
        )
    active_count = await _register_active_ws_entry()
    if active_count is not None:
        with contextlib.suppress(Exception):
            _jlog("ws_conn_active", conn_id=conn_id, sid=sid, active=active_count)
    with contextlib.suppress(Exception):
        if _admin_emit:
            _admin_emit(
                "ws_conn_open",
                sid=sid,
                conn_id=conn_id,
                client_ip=client_ip,
                path=scope.get("path"),
                query_string=raw_query,
            )
            if active_count is not None:
                _admin_emit("ws_conn_active", conn_id=conn_id, active=active_count)

    with contextlib.suppress(Exception):
        clear_greet_turn_cache(sid)

    bus_task = asyncio.create_task(_pump_bus_to_client(sid, send))

    async def _ping_loop():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await _ws_send_json(
                        send, {"type": "keepalive", "ts": int(time.time() * 1000)}
                    )
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    ping_task = asyncio.create_task(_ping_loop())

    try:
        await _ws_send_json(send, {"type": "ready", "session_id": sid})
    except Exception:
        with contextlib.suppress(Exception):
            await send(
                {
                    "type": "websocket.close",
                    "code": 1011,
                    "reason": "initial_ready_failed",
                }
            )
        return

    cfg: Dict[str, Any] = {}
    loop = asyncio.get_running_loop()
    barge = BargeState()

    def _send_barge_state(phase: str) -> None:
        if not phase:
            return
        try:
            set_phase(sid, phase)
        except Exception:
            pass
        if phase == "paused":
            try:
                set_recorder_active(sid, True)
            except Exception:
                pass
            nudges.cancel_idle_timers(sid, source="ws_state_paused")
        frame = {"type": "state", "phase": phase}
        try:
            bus.broadcast(sid, frame)
        except Exception:
            pass
        try:
            if loop.is_closed():
                return
            payload = dict(frame)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                asyncio.create_task(_ws_send_json(send, payload))
            else:
                loop.call_soon_threadsafe(
                    asyncio.create_task, _ws_send_json(send, payload)
                )
        except Exception:
            pass

    buf = TurnBuffer()
    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None
    dg_connect_task: Optional[asyncio.Task] = None
    dg_state: str = "closed"
    turn_id_ref = [0]
    pending_final_turns: Deque[int] = deque()
    completed_llm_turns: Set[int] = set()
    synthetic_final_turns: Set[int] = set()
    final_seen = [False]
    asr_seen_partial = [False]
    turn_timing: Dict[str, List[float]] = {
        "start": [0.0],
        "dg_open": [0.0],
        "first_partial": [0.0],
        "final": [0.0],
    }
    turn_finish_logged = [False]

    # Turn-scoped buffering + state
    buffered_chunks: Deque[bytes] = deque()
    sent_any_audio = [False]
    no_audio_watch_task: List[Optional[asyncio.Task]] = [None]
    no_audio_notified = [False]
    no_audio_turn_id = [0]
    try:
        no_audio_window_s = float(
            os.getenv("WS_NO_AUDIO_DETECT_WINDOW_S", "10.0") or "0"
        )
    except Exception:
        no_audio_window_s = 10.0
    if no_audio_window_s < 0:
        no_audio_window_s = 0.0
    no_audio_broadcast_enabled = _env_truth("WS_NO_AUDIO_NUDGE", True)
    asr_ready_evt: asyncio.Event = asyncio.Event()
    asr_ready_wait_s: float = float(os.getenv("ASR_READY_WAIT_S", "3.0"))
    max_buffered_chunks = max(1, int(os.getenv("ASR_MAX_BUFFERED_CHUNKS", "16")))
    ws_frames_in = 0
    ws_bytes_in = 0
    backpressure_drop_count = 0
    backpressure_last_emit = 0.0
    backpressure_last_queue_len = 0
    backpressure_emit_interval = 1.0
    turn_connect_started = [False]

    # NEW: per-turn mic capture state
    mic_chunks: List[bytes] = []
    mic_first_ts = [0.0]
    mic_last_ts = [0.0]

    def _reset_turn_metrics(start_ts: float) -> None:
        turn_timing["start"][0] = start_ts
        turn_timing["dg_open"][0] = 0.0
        turn_timing["first_partial"][0] = 0.0
        turn_timing["final"][0] = 0.0
        turn_finish_logged[0] = False

    def _emit_no_audio_alert(reason: str) -> None:
        if no_audio_notified[0]:
            return
        if sent_any_audio[0]:
            return
        no_audio_notified[0] = True
        turn_id = no_audio_turn_id[0] or turn_id_ref[0] or 0
        since_first_ms = None
        if mic_first_ts[0]:
            since_first_ms = int(max(0.0, (time.time() - mic_first_ts[0]) * 1000))
        with contextlib.suppress(Exception):
            _jlog(
                "ws_no_audio_detected",
                sid=sid,
                turn_id=turn_id,
                reason=reason,
                window_s=no_audio_window_s,
                mic_chunks=len(mic_chunks),
                buffered=len(buffered_chunks),
                since_first_ms=since_first_ms,
            )
        if _admin_emit:
            with contextlib.suppress(Exception):
                _admin_emit(
                    "no_audio_detected",
                    session_id=sid,
                    turn_id=turn_id,
                    reason=reason,
                    window_s=no_audio_window_s,
                    mic_chunks=len(mic_chunks),
                    buffered_chunks=len(buffered_chunks),
                    since_first_chunk_ms=since_first_ms,
                )
        if no_audio_broadcast_enabled:
            frame = {
                "type": "no_audio_detected",
                "turn_id": str(turn_id),
                "window_s": no_audio_window_s,
                "reason": reason,
            }
            if since_first_ms is not None:
                frame["since_first_chunk_ms"] = since_first_ms
            with contextlib.suppress(Exception):
                bus.broadcast(sid, frame)

    def _cancel_no_audio_watch() -> None:
        task = no_audio_watch_task[0]
        if task and not task.done():
            task.cancel()
        no_audio_watch_task[0] = None

    def _schedule_no_audio_watch(turn_id: int) -> None:
        if no_audio_window_s <= 0:
            return
        _cancel_no_audio_watch()
        no_audio_notified[0] = False
        no_audio_turn_id[0] = turn_id

        async def _watch() -> None:
            try:
                await asyncio.sleep(no_audio_window_s)
                if sent_any_audio[0] or no_audio_notified[0]:
                    return
                _emit_no_audio_alert("timeout")
            except asyncio.CancelledError:
                pass
            finally:
                no_audio_watch_task[0] = None

        no_audio_watch_task[0] = asyncio.create_task(_watch())

    def _log_turn_finish(
        turn_id: int, reason: str, synthetic: bool, transcript_chars: int
    ) -> None:
        if turn_finish_logged[0]:
            return
        turn_finish_logged[0] = True
        now_ts = time.time()
        turn_timing["final"][0] = now_ts
        start_ts = turn_timing.get("start", [0.0])[0]
        dg_open_ts = turn_timing.get("dg_open", [0.0])[0]
        first_partial_ts = turn_timing.get("first_partial", [0.0])[0]
        delta_start = int((now_ts - start_ts) * 1000) if start_ts else None
        delta_open = int((now_ts - dg_open_ts) * 1000) if dg_open_ts else None
        delta_partial = (
            int((now_ts - first_partial_ts) * 1000) if first_partial_ts else None
        )
        with contextlib.suppress(Exception):
            _jlog(
                "turn_finish",
                sid=sid,
                turn_id=turn_id,
                reason=reason,
                synthetic=bool(synthetic),
                delta_start_ms=delta_start,
                delta_asr_open_ms=delta_open,
                delta_first_partial_ms=delta_partial,
                transcript_chars=int(transcript_chars),
            )

    async def _emit_synthetic_final(
        turn_id: int, reason: str, transcript: str = ""
    ) -> bool:
        if final_seen[0]:
            with contextlib.suppress(Exception):
                _jlog(
                    "ws_synthetic_final_skip_duplicate",
                    sid=sid,
                    turn_id=turn_id,
                    reason=reason,
                )
            return False
        final_seen[0] = True
        payload = make_results(turn_id, transcript=transcript, is_final=True)
        payload["type"] = "Results"
        await _ws_send_json(send, payload)
        utterance_payload = make_utterance_end(turn_id)
        utterance_payload["type"] = "UtteranceEnd"
        await _ws_send_json(send, utterance_payload)
        with contextlib.suppress(Exception):
            _jlog("ws_synthetic_final", sid=sid, turn_id=turn_id, reason=reason)
        _log_turn_finish(
            turn_id,
            reason=reason,
            synthetic=True,
            transcript_chars=len(transcript or ""),
        )
        with contextlib.suppress(Exception):
            note_utterance_end(sid)
        with contextlib.suppress(Exception):
            set_recorder_active(sid, False)
        completed_llm_turns.discard(turn_id)
        return True

    async def _ensure_dg_connected() -> bool:
        nonlocal dg, rx_task, dg_connect_task, dg_state, fallback_detection

        if not _has_deepgram_key():
            with contextlib.suppress(Exception):
                _jlog("asr_connect_skip", sid=sid, reason="no_api_key")
            return False

        if dg_state == "open" and dg is not None:
            return True

        if dg_state == "connecting" and dg_connect_task is not None:
            with contextlib.suppress(Exception):
                _jlog("asr_connect_wait", sid=sid, reason="existing_task")
            with contextlib.suppress(Exception):
                await dg_connect_task
            return dg_state == "open" and dg is not None

        connect_result = {"ok": False}

        async def _connect() -> None:
            nonlocal dg, rx_task, dg_connect_task, dg_state, connect_result, fallback_detection
            try:
                dg_state = "connecting"
                with contextlib.suppress(Exception):
                    asr_ready_evt.clear()

                if not raw_fallback_disabled() and should_use_raw_fallback(
                    fallback_detection, stream_stats
                ):
                    if not stream_stats.forced_fallback:
                        stream_stats.force_fallback()
                        overrides = coerce_to_raw_config(stream_meta)
                        for key, value in overrides.items():
                            if key == "_transport":
                                continue
                            cfg[key] = value
                        transport.update(overrides.get("_transport", {}))
                        fallback_detection = RawDetectionSignal(
                            container="raw",
                            codec="pcm",
                            containerized=False,
                            source="raw_fallback",
                        )
                        stream_stats.note_detection(fallback_detection)
                        with contextlib.suppress(Exception):
                            _jlog(
                                "raw_fallback_applied",
                                sid=sid,
                                sample_rate=transport.get("sample_rate"),
                                channels=transport.get("channels"),
                            )
                        if buffered_chunks:
                            normalized_buffer: Deque[bytes] = deque()
                            for existing in list(buffered_chunks):
                                normalized = normalize_pcm_frame(existing, stream_meta)
                                if normalized != existing:
                                    stream_stats.note_jitter_slip()
                                normalized_buffer.append(normalized)
                            buffered_chunks.clear()
                            buffered_chunks.extend(normalized_buffer)
                        if MIC_CAPTURE and mic_chunks:
                            for idx, existing in enumerate(list(mic_chunks)):
                                normalized = normalize_pcm_frame(existing, stream_meta)
                                if normalized != existing:
                                    stream_stats.note_jitter_slip()
                                mic_chunks[idx] = normalized

                cfg["_transport"] = transport
                cfg["_jlog"] = _jlog
                cfg.setdefault("session_id", sid)
                cfg["_url_tag"] = f"{WS_ASGI_BUILD}:{sid}"
                nudges.cancel_idle_timers(sid, source="asr_connect_begin")
                _jlog("asr_connect_begin", sid=sid, transport=transport)
                client = DeepgramClient(cfg)
                dg = client
                await client.connect()
                nudges.cancel_idle_timers(sid, source="asr_connect_ok")
                try:
                    set_asr_stream_open(sid, True)
                except Exception:
                    pass
                dg_state = "open"
                connect_result["ok"] = True
                turn_id_ref[0] = buf.turn_seq + 1
                rx_task = asyncio.create_task(
                    _pump_dg_to_client(
                        client,
                        send,
                        turn_id_ref,
                        final_seen,
                        pending_final_turns,
                        synthetic_final_turns,
                        completed_llm_turns,
                        sid,
                        asr_ready_evt,
                        _flush_buffered_chunks,
                        stream_stats,
                        turn_timing,
                        _log_turn_finish,
                    )
                )
                _jlog("asr_connect_ok", sid=sid)
            except Exception as e:
                dg_state = "closed"
                dg = None
                try:
                    set_asr_stream_open(sid, False)
                except Exception:
                    pass
                _jlog("asr_connect_fail", sid=sid, err=type(e).__name__)
                with contextlib.suppress(Exception):
                    await _ws_send_json(
                        send, make_error("asr_connect_fail", type(e).__name__)
                    )
                with contextlib.suppress(Exception):
                    _admin_emit and _admin_emit(
                        "asr:error", session_id=sid, error=f"connect:{type(e).__name__}"
                    )
            finally:
                dg_connect_task = None
                try:
                    set_asr_stream_open(sid, dg_state == "open")
                except Exception:
                    pass

        dg_connect_task = asyncio.create_task(_connect())
        with contextlib.suppress(Exception):
            await dg_connect_task
        return connect_result["ok"]

    def _dg_client_ready(client: Optional[DeepgramClient]) -> bool:
        """Return True if the Deepgram client reports an open websocket."""
        if client is None:
            return False
        try:
            is_open = getattr(client, "is_open", None)
            if callable(is_open) and is_open():
                return True
        except Exception:
            pass
        evt = getattr(client, "_open_evt", None)
        if isinstance(evt, asyncio.Event):
            try:
                return evt.is_set()
            except Exception:
                return False
        return False

    def _maybe_emit_backpressure(
        queue_len: int, *, dropped_now: int = 0, force: bool = False
    ) -> None:
        nonlocal backpressure_drop_count, backpressure_last_emit, backpressure_last_queue_len

        previous_drop_count = backpressure_drop_count
        if dropped_now:
            backpressure_drop_count += dropped_now

        now = time.time()
        threshold_crossed = (
            queue_len >= max_buffered_chunks
            and backpressure_last_queue_len < max_buffered_chunks
        )
        should_emit = force or threshold_crossed or dropped_now > 0

        if should_emit:
            if (
                force
                or threshold_crossed
                or now - backpressure_last_emit >= backpressure_emit_interval
                or (dropped_now > 0 and previous_drop_count == 0)
            ):
                payload = dict(
                    sid=sid,
                    queue_len=queue_len,
                    dropped=backpressure_drop_count,
                )
                with contextlib.suppress(Exception):
                    _jlog("ws_backpressure", **payload)
                if _admin_emit:
                    with contextlib.suppress(Exception):
                        _admin_emit("ws_backpressure", **payload)
                backpressure_last_emit = now

        backpressure_last_queue_len = queue_len

    async def _send_chunk(
        data: bytes, *, from_buffer: bool = False, retry: bool = True
    ) -> bool:
        nonlocal dg, dg_state
        if dg is None:
            return False
        try:
            await dg.send(data)
            sent_any_audio[0] = True
            _cancel_no_audio_watch()
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
        nonlocal dg, backpressure_drop_count
        if not buffered_chunks:
            return
        if not _has_deepgram_key() or dg is None:
            return
        initial_queued = len(buffered_chunks)
        flushed_chunks = 0
        flushed_bytes = 0
        with contextlib.suppress(Exception):
            _jlog(
                "ws_flush_request",
                sid=sid,
                queued=initial_queued,
                dg_ready=_dg_client_ready(dg),
            )
        while buffered_chunks:
            chunk = buffered_chunks[0]
            chunk_len = len(chunk) if isinstance(chunk, (bytes, bytearray)) else 0
            ok = await _send_chunk(chunk, from_buffer=True)
            if ok:
                buffered_chunks.popleft()
                flushed_chunks += 1
                flushed_bytes += chunk_len
                continue
            break
        with contextlib.suppress(Exception):
            _jlog(
                "ws_flush_done",
                sid=sid,
                queued=initial_queued,
                flushed_chunks=flushed_chunks,
                flushed_bytes=flushed_bytes,
                remaining=len(buffered_chunks),
            )
        _maybe_emit_backpressure(len(buffered_chunks))
        if not buffered_chunks:
            backpressure_drop_count = 0

    try:
        while True:
            ev = await receive()
            et = ev.get("type")

            if et == "websocket.disconnect":
                had_disconnect = True
                now = time.time()
                idle_s = (
                    max(0.0, now - last_msg_ts) if last_msg_ts is not None else None
                )
                with contextlib.suppress(Exception):
                    _jlog(
                        "ws_conn_disconnect",
                        conn_id=conn_id,
                        sid=sid,
                        code=ev.get("code"),
                        reason=ev.get("reason"),
                        idle_s=idle_s,
                    )
                with contextlib.suppress(Exception):
                    if _admin_emit:
                        _admin_emit(
                            "ws_conn_disconnect",
                            conn_id=conn_id,
                            sid=sid,
                            code=ev.get("code"),
                            reason=ev.get("reason"),
                            idle_s=idle_s,
                        )
                await _remove_active_ws_entry("disconnect")
                last_msg_ts = now
                break

            if et == "websocket.receive":
                last_msg_ts = time.time()
                # -------------------- Binary / audio lane --------------------
                if ev.get("bytes") is not None:
                    now = time.time()
                    frame_bytes = ev.get("bytes")
                    chunk = frame_bytes or b""
                    ws_frames_in += 1
                    ws_bytes_in += len(chunk)
                    if ws_frames_in % 20 == 0:
                        with contextlib.suppress(Exception):
                            _jlog(
                                "ws_bin_recv",
                                sid=sid,
                                frames_in=ws_frames_in,
                                bytes_total=ws_bytes_in,
                            )
                        if _admin_emit:
                            with contextlib.suppress(Exception):
                                _admin_emit(
                                    "ws_bin_recv",
                                    sid=sid,
                                    frames_in=ws_frames_in,
                                    bytes_total=ws_bytes_in,
                                )
                    if chunk:
                        _jlog("ws_audio_chunk", sid=sid, bytes=len(chunk))
                        if not audio_sig_logged:
                            with contextlib.suppress(Exception):
                                _jlog("audio_sig", sid=sid, first8_hex=chunk[:8].hex())
                            audio_sig_logged = True
                    else:
                        with contextlib.suppress(Exception):
                            _jlog(
                                "ws_audio_chunk_empty",
                                sid=sid,
                                bytes=len(frame_bytes or b""),
                                turn_id=turn_id_ref[0],
                                buf_empty=buf.is_empty(),
                            )

                    raw_chunk = chunk

                    if buf.is_empty():
                        nudges.cancel_idle_timers(sid, source="turn_start")
                        try:
                            set_recorder_active(sid, True)
                        except Exception:
                            pass
                        # New audio turn
                        current_assistant_turn = None
                        try:
                            current_assistant_turn = bus.current_assistant_turn(sid)
                        except Exception:
                            current_assistant_turn = None
                        confirm_ms = 420
                        try:
                            confirm_ms = int(cfg.get("confirm_ms", 420) or 0)
                        except Exception:
                            confirm_ms = 420

                        def _on_barge_commit() -> None:
                            target_turn = current_assistant_turn
                            try:
                                latest = bus.current_assistant_turn(sid)
                            except Exception:
                                latest = None
                            if latest:
                                target_turn = latest
                            if target_turn:
                                try:
                                    bus.cancel_turn(sid, target_turn)
                                except Exception:
                                    pass
                        with contextlib.suppress(Exception):
                            barge.start(
                                confirm_ms=confirm_ms,
                                on_commit=_on_barge_commit,
                                send_state=_send_barge_state,
                            )
                        turn_id_ref[0] = buf.turn_seq + 1
                        with contextlib.suppress(Exception):
                            pending_final_turns.append(turn_id_ref[0])
                            completed_llm_turns.discard(turn_id_ref[0])
                        final_seen[0] = False
                        asr_seen_partial[0] = False
                        sent_any_audio[0] = False
                        buffered_chunks.clear()
                        stream_stats.reset_turn()
                        turn_connect_started[0] = False
                        # reset mic capture
                        mic_chunks.clear()
                        mic_first_ts[0] = now
                        mic_last_ts[0] = now
                        _reset_turn_metrics(now)
                        _schedule_no_audio_watch(turn_id_ref[0])
                        with contextlib.suppress(Exception):
                            _jlog(
                                "turn_start",
                                sid=sid,
                                turn_id=turn_id_ref[0],
                                first_bytes=len(raw_chunk),
                            )
                    # Detect container early using raw bytes

                    # Detect container early
                    try:
                        if transport.get("container") is None and raw_chunk:
                            det = sniffer.feed(raw_chunk)
                            if det:
                                container = getattr(det, "container", None)
                                codec = getattr(det, "codec", None)
                                containerized = bool(
                                    getattr(det, "containerized", codec == "opus")
                                )
                                transport["container"] = container
                                transport["codec"] = codec
                                transport["containerized_opus"] = containerized
                                fallback_detection = RawDetectionSignal(
                                    container=container,
                                    codec=codec,
                                    containerized=containerized,
                                    source="sniffer",
                                )
                                stream_stats.note_detection(fallback_detection)
                                stream_meta.encoding = "opus"
                                _jlog(
                                    "sniffer_detect",
                                    sid=sid,
                                    container=transport.get("container"),
                                    codec=transport.get("codec"),
                                    containerized_opus=transport.get(
                                        "containerized_opus"
                                    ),
                                )
                            else:
                                meta_det = coerce_detection_from_meta(
                                    getattr(sniffer, "meta", lambda: None)()
                                )
                                if meta_det and getattr(meta_det, "container", None):
                                    container = getattr(meta_det, "container", None)
                                    codec = getattr(meta_det, "codec", None)
                                    containerized = bool(
                                        getattr(
                                            meta_det, "containerized", codec == "opus"
                                        )
                                    )
                                    transport["container"] = container
                                    transport["codec"] = codec
                                    transport["containerized_opus"] = containerized
                                    fallback_detection = RawDetectionSignal(
                                        container=container,
                                        codec=codec,
                                        containerized=containerized,
                                        source="mime",
                                    )
                                    stream_stats.note_detection(fallback_detection)
                                    stream_meta.encoding = "opus"
                                    _jlog(
                                        "sniffer_detect",
                                        sid=sid,
                                        container=transport.get("container"),
                                        codec=transport.get("codec"),
                                        containerized_opus=transport.get(
                                            "containerized_opus"
                                        ),
                                    )
                    except Exception:
                        pass

                    chunk = stream_stats.observe_frame(raw_chunk)

                    buf.append(chunk)

                    # capture bytes for diagnostic playback
                    if MIC_CAPTURE:
                        mic_chunks.append(chunk)
                        mic_last_ts[0] = now
                    _jlog(
                        "mic_capture_append",
                        sid=sid,
                        turn_id=turn_id_ref[0],
                        chunks=len(mic_chunks),
                        last_bytes=len(chunk),
                    )
                    nudges.cancel_idle_timers(sid, source="mic_capture")

                    if not _has_deepgram_key():
                        _jlog("ws_audio_no_key", sid=sid, bytes=len(chunk))
                        continue

                    # Stage early frames
                    if chunk:
                        buffered_chunks.append(chunk)
                        dropped_now = 0
                        if len(buffered_chunks) > max_buffered_chunks:
                            dropped_now = len(buffered_chunks) - max_buffered_chunks
                            for _ in range(dropped_now):
                                if not buffered_chunks:
                                    break
                                buffered_chunks.popleft()
                            _jlog(
                                "ws_audio_drop", sid=sid, dropped=dropped_now, queued=len(buffered_chunks)
                            )
                        _maybe_emit_backpressure(len(buffered_chunks), dropped_now=dropped_now)

                    # Ensure provider connection
                    if not turn_connect_started[0] and dg_state == "closed":
                        turn_connect_started[0] = True
                        if dg_connect_task is None:
                            with contextlib.suppress(Exception):
                                _jlog(
                                    "asr_connect_schedule",
                                    sid=sid,
                                    turn_id=turn_id_ref[0],
                                    queued=len(buffered_chunks),
                                    dg_state=dg_state,
                                )
                            dg_connect_task = asyncio.create_task(
                                _ensure_dg_connected()
                            )
                    elif dg_state == "connecting":
                        pass

                    # Flush when ready
                    if dg is not None:
                        if not asr_ready_evt.is_set():
                            try:
                                await asyncio.wait_for(
                                    asr_ready_evt.wait(), timeout=asr_ready_wait_s
                                )
                            except asyncio.TimeoutError:
                                _jlog("asr_not_ready_timeout", sid=sid)
                        if len(buffered_chunks) >= max_buffered_chunks:
                            await _flush_buffered_chunks()
                        await _flush_buffered_chunks()
                    else:
                        _jlog(
                            "ws_audio_provider_connecting",
                            sid=sid,
                            queued=len(buffered_chunks),
                        )
                    continue

                # -------------------- Text / control lane --------------------
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")
                        with contextlib.suppress(Exception):
                            _jlog("ws_json_recv", sid=sid, type=t)
                        if _admin_emit:
                            with contextlib.suppress(Exception):
                                _admin_emit("ws_json_recv", sid=sid, type=t)

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
                                            audio_on = bool(
                                                (cfg_now or {}).get(
                                                    "feature_audio", True
                                                )
                                            )
                                            _admin_emit(
                                                "greet:resp",
                                                label="greet:resp",
                                                session_id=sid,
                                                turn_id=tid,
                                                audio_scheduled=audio_on,
                                            )
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(
                                            send,
                                            make_error(
                                                "greet_fail", e.__class__.__name__
                                            ),
                                        )

                            asyncio.create_task(_bg())

                        elif t == "Configure":
                            cfg.update(obj or {})
                            stream_meta.apply_config(obj or {})
                            enc_lower = (obj.get("encoding") or "").strip().lower()
                            if enc_lower == "pcm":
                                fallback_detection = RawDetectionSignal(
                                    container="raw",
                                    codec="pcm",
                                    containerized=False,
                                    source="configure",
                                )
                                stream_stats.note_detection(fallback_detection)
                            if obj.get("reset"):
                                with contextlib.suppress(Exception):
                                    clear_greet_turn_cache(sid)
                                with contextlib.suppress(Exception):
                                    _admin_emit and _admin_emit(
                                        "greet:reset",
                                        route="/ws/v1/chat",
                                        label="greet:reset",
                                        session_id=sid,
                                    )
                            if obj.get("greet"):
                                _jlog("ws_greet_recv", sid=sid, via="Configure")

                                async def _bg2():
                                    try:
                                        from app.services.streaming import run_ws_greet

                                        tid = await asyncio.to_thread(run_ws_greet, sid)
                                        with contextlib.suppress(Exception):
                                            if _admin_emit:
                                                cfg_now = db.get_config()
                                                audio_on = bool(
                                                    (cfg_now or {}).get(
                                                        "feature_audio", True
                                                    )
                                                )
                                                _admin_emit(
                                                    "greet:resp",
                                                    label="greet:resp",
                                                    session_id=sid,
                                                    turn_id=tid,
                                                    audio_scheduled=audio_on,
                                                )
                                    except Exception as e:
                                        with contextlib.suppress(Exception):
                                            await _ws_send_json(
                                                send,
                                                make_error(
                                                    "greet_fail", e.__class__.__name__
                                                ),
                                            )

                                asyncio.create_task(_bg2())

                        elif t in (
                            "user_msg",
                            "User",
                            "UserText",
                            "UserMessage",
                            "UserUtterance",
                            "UserTextMessage",
                        ):
                            text = (obj.get("text") or "").strip()
                            if not text:
                                continue
                            if len(text) > 8000:
                                await _ws_send_json(
                                    send, make_error("payload_too_large", "user_text")
                                )
                                continue
                            corr = obj.get("correlation_user_msg_id") or obj.get(
                                "userMsgId"
                            )
                            _jlog(
                                "ws_user_msg_recv",
                                sid=sid,
                                text_len=len(text),
                                corr=bool(corr),
                            )

                            nudges.cancel_idle_timers(sid)

                            dialog_nlu_pre: Dict[str, Any] = {}
                            universal_pre: Dict[str, Any] = {}
                            try:
                                prepared_meta, dialog_nlu_raw, _ = prepare_turn_metadata(
                                    text, {"source": "user_ws", "channel": "ws"}
                                )
                                if isinstance(dialog_nlu_raw, dict):
                                    dialog_nlu_pre = dict(dialog_nlu_raw)
                                if isinstance(prepared_meta, dict):
                                    raw_universal = prepared_meta.get("universal") or {}
                                    if isinstance(raw_universal, dict):
                                        universal_pre = _ensure_universal_fields(raw_universal)
                            except Exception:
                                dialog_nlu_pre = {}
                                universal_pre = {}

                            _emit_admin_nlu_event(
                                text,
                                sid,
                                dialog_nlu=dialog_nlu_pre,
                                universal=universal_pre,
                            )

                            meta_overrides: Optional[Dict[str, Any]] = None
                            if dialog_nlu_pre or universal_pre:
                                meta_overrides = {}
                                if dialog_nlu_pre:
                                    meta_overrides["nlu"] = dict(dialog_nlu_pre)
                                    meta_overrides["dialog_nlu"] = dict(dialog_nlu_pre)
                                if universal_pre:
                                    meta_overrides["universal"] = dict(universal_pre)

                            async def _bg_user():
                                try:
                                    await asyncio.to_thread(
                                        run_ws_user_turn,
                                        sid,
                                        text,
                                        corr,
                                        meta_overrides=meta_overrides,
                                    )
                                except Exception as e:
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(
                                            send,
                                            make_error(
                                                "user_fail", e.__class__.__name__
                                            ),
                                        )

                            asyncio.create_task(_bg_user())

                        elif t == "activity":
                            kind = (obj.get("kind") or "").strip().lower()
                            if kind in {"speech", "text", "chip"}:
                                nudges.cancel_idle_timers(sid)
                            continue

                        elif t == "CloseStream":
                            _jlog("ws_close_stream", sid=sid)
                            nudges.cancel_idle_timers(sid)

                            # Always define this first so later 'if synthetic_emitted' is safe
                            synthetic_emitted = False

                            if buf.is_empty():
                                # Empty turn closure; synthesize ids + reset final tracking.
                                turn_id_ref[0] = buf.turn_seq + 1
                                final_seen[0] = False
                                _reset_turn_metrics(time.time())
                                with contextlib.suppress(Exception):
                                    _jlog(
                                        "turn_start",
                                        sid=sid,
                                        turn_id=turn_id_ref[0],
                                        first_bytes=0,
                                        empty_turn=True,
                                    )

                            # --- Get a turn_id (with guard logs) ---
                            _jlog(
                                "before_close_turn",
                                sid=sid,
                                next_turn_id=buf.turn_seq + 1,
                            )
                            try:
                                turn_id, _pcm = buf.close_turn()
                            except Exception as e:
                                _jlog("close_turn_fail", sid=sid, err=type(e).__name__)
                                turn_id = turn_id_ref[0] or (buf.turn_seq + 1)
                                _pcm = None
                            _jlog("after_close_turn", sid=sid, turn_id=turn_id)
                            turn_id_ref[0] = turn_id

                            # ---- THEN finish the ASR turn (unchanged logic) ----
                            if _has_deepgram_key():
                                # If provider isn't ready yet but we have audio, try to connect now (bounded wait)
                                if dg is None and (buffered_chunks or mic_chunks):
                                    if dg_connect_task is None:
                                        dg_connect_task = asyncio.create_task(
                                            _ensure_dg_connected()
                                        )
                                    with contextlib.suppress(asyncio.TimeoutError):
                                        await asyncio.wait_for(
                                            asr_ready_evt.wait(), timeout=1.2
                                        )

                                # If we have buffered chunks but ASR not ready yet, give it a moment then flush.
                                if buffered_chunks and not asr_ready_evt.is_set():
                                    if (
                                        dg_state == "connecting"
                                        and dg_connect_task is not None
                                    ):
                                        with contextlib.suppress(Exception):
                                            await asyncio.wait_for(
                                                dg_connect_task,
                                                timeout=asr_ready_wait_s,
                                            )
                                    with contextlib.suppress(asyncio.TimeoutError):
                                        await asyncio.wait_for(
                                            asr_ready_evt.wait(),
                                            timeout=asr_ready_wait_s,
                                        )

                                # Flush any staged audio first
                                await _flush_buffered_chunks()

                                # Give ASR a brief chance to be "ready", then flush again
                                with contextlib.suppress(asyncio.TimeoutError):
                                    await asyncio.wait_for(
                                        asr_ready_evt.wait(), timeout=1.0
                                    )

                                await _flush_buffered_chunks()

                                # Optional tiny settle after flush if no partials yet (helps very short clips)
                                if not final_seen[0] and not asr_seen_partial[0]:
                                    with contextlib.suppress(Exception):
                                        await asyncio.sleep(
                                            float(
                                                os.getenv(
                                                    "ASR_POST_FLUSH_WAIT_S", "0.35"
                                                )
                                            )
                                        )

                                provider_can_close = False
                                fallback_reason = "no_audio"
                                if dg is not None and sent_any_audio[0]:
                                    fallback_reason = None
                                    readiness_timeout = False
                                    if (
                                        dg_connect_task is not None
                                        and not dg_connect_task.done()
                                    ):
                                        try:
                                            await asyncio.wait_for(
                                                dg_connect_task,
                                                timeout=asr_ready_wait_s,
                                            )
                                        except asyncio.TimeoutError:
                                            readiness_timeout = True
                                            _jlog(
                                                "ws_close_dg_connect_timeout", sid=sid
                                            )
                                        except Exception as exc:
                                            _jlog(
                                                "ws_close_dg_connect_error",
                                                sid=sid,
                                                err=type(exc).__name__,
                                            )
                                    if (
                                        asr_ready_evt is not None
                                        and not asr_ready_evt.is_set()
                                    ):
                                        try:
                                            await asyncio.wait_for(
                                                asr_ready_evt.wait(),
                                                timeout=asr_ready_wait_s,
                                            )
                                        except asyncio.TimeoutError:
                                            readiness_timeout = True
                                            _jlog("ws_close_dg_ready_timeout", sid=sid)
                                    provider_open = _dg_client_ready(dg)
                                    if not provider_open:
                                        _jlog("ws_close_dg_not_open", sid=sid)
                                    sent_any_audio[0] = (
                                        sent_any_audio[0]
                                        and provider_open
                                        and not readiness_timeout
                                    )
                                    if not sent_any_audio[0]:
                                        if readiness_timeout or not provider_open:
                                            fallback_reason = "dg_not_ready"
                                        else:
                                            fallback_reason = "no_audio"
                                    else:
                                        provider_can_close = True
                                elif (
                                    fallback_reason == "dg_not_ready"
                                    and dg_connect_task is not None
                                ):
                                    if not dg_connect_task.done():
                                        with contextlib.suppress(Exception):
                                            await asyncio.wait_for(
                                                dg_connect_task,
                                                timeout=asr_ready_wait_s,
                                            )
                                    if dg is not None and sent_any_audio[0]:
                                        fallback_reason = None
                                        provider_can_close = True
                                if provider_can_close:
                                    # Ask provider to finish; if no final came, we'll synthesize after
                                    drain_timeout_exc: Optional[
                                        DeepgramDrainTimeoutError
                                    ] = None
                                    try:
                                        await asyncio.sleep(
                                            float(
                                                os.getenv("ASR_FINAL_GRACE_S", "0.30")
                                            )
                                        )  # ~300 ms grace
                                        await dg.close(wait_for_final=True)
                                    except DeepgramDrainTimeoutError as exc:
                                        drain_timeout_exc = exc
                                        _jlog(
                                            "ws_close_dg_drain_timeout",
                                            sid=sid,
                                            queued_chunks=getattr(
                                                exc, "queued_chunks", None
                                            ),
                                            queued_bytes=getattr(
                                                exc, "queued_bytes", None
                                            ),
                                            wait_timeout=getattr(
                                                exc, "wait_timeout", None
                                            ),
                                        )
                                    except Exception as exc:
                                        _jlog(
                                            "ws_close_dg_close_error",
                                            sid=sid,
                                            err=type(exc).__name__,
                                        )
                                    dg_state = "closed"
                                    try:
                                        set_asr_stream_open(sid, False)
                                    except Exception:
                                        pass
                                    _relay_task = rx_task
                                    rx_task = None
                                    if _relay_task:
                                        _relay_task.cancel()
                                        with contextlib.suppress(
                                            asyncio.CancelledError, Exception
                                        ):
                                            await _relay_task
                                    dg = None
                                    with contextlib.suppress(Exception):
                                        asr_ready_evt.clear()
                                    if not final_seen[0]:
                                        synth_reason = "dg_close_no_final"
                                        if drain_timeout_exc is not None:
                                            synth_reason = "dg_drain_timeout"
                                        if await _emit_synthetic_final(
                                            turn_id, synth_reason
                                        ):
                                            synthetic_emitted = True
                                            with contextlib.suppress(ValueError):
                                                pending_final_turns.remove(turn_id)
                                            synthetic_final_turns.add(turn_id)
                                    if drain_timeout_exc is not None:
                                        _jlog(
                                            "ws_close_dg_drain_timeout_fallback",
                                            sid=sid,
                                            queued_chunks=getattr(
                                                drain_timeout_exc, "queued_chunks", None
                                            ),
                                            queued_bytes=getattr(
                                                drain_timeout_exc, "queued_bytes", None
                                            ),
                                        )
                                else:
                                    if fallback_reason == "dg_not_ready":
                                        _jlog("ws_close_fallback_not_ready", sid=sid)
                                    else:
                                        _jlog("ws_close_skip_no_audio", sid=sid)
                                    if not final_seen[0]:
                                        reason = (
                                            "fallback_dg_not_ready"
                                            if fallback_reason == "dg_not_ready"
                                            else "fallback_no_audio"
                                        )
                                        if await _emit_synthetic_final(turn_id, reason):
                                            synthetic_emitted = True
                                            with contextlib.suppress(ValueError):
                                                pending_final_turns.remove(turn_id)
                                            synthetic_final_turns.add(turn_id)
                            else:
                                # No provider configured: still emit empty final + end to advance the dialog.
                                if not final_seen[0]:
                                    if await _emit_synthetic_final(
                                        turn_id, "no_provider"
                                    ):
                                        synthetic_emitted = True
                                        with contextlib.suppress(ValueError):
                                            pending_final_turns.remove(turn_id)
                                        synthetic_final_turns.add(turn_id)

                            _cancel_no_audio_watch()
                            if (not sent_any_audio[0]) and not no_audio_notified[0]:
                                elapsed = None
                                if mic_first_ts[0]:
                                    elapsed = time.time() - mic_first_ts[0]
                                if no_audio_window_s <= 0 or (
                                    elapsed is not None and elapsed >= no_audio_window_s
                                ):
                                    _emit_no_audio_alert("close_stream")

                            if synthetic_emitted:
                                # Reset so the next turn starts fresh even if no audio chunk arrives.
                                final_seen[0] = False

                            if barge.is_paused():
                                with contextlib.suppress(Exception):
                                    barge.cancel(_send_barge_state)
                                await asyncio.sleep(0)

                            # ---- NEW: mic-capture summary, save to /tmp (or $TMPDIR), and optional WS echo ----

                            _jlog(
                                "mic_capture_block_enter",
                                sid=sid,
                                turn_id=turn_id,
                                mic_chunks=len(mic_chunks),
                            )
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
                                        containerized_opus=transport.get(
                                            "containerized_opus"
                                        ),
                                    )

                                    if raw:
                                        base_dir = os.getenv("TMPDIR") or "/tmp"
                                        if transport.get("containerized_opus"):
                                            # Containerized Opus → save WebM bytes as-is
                                            out_path = os.path.join(
                                                base_dir, f"mic_{sid}_{turn_id}.webm"
                                            )
                                            mime = "audio/webm"
                                            with open(out_path, "wb") as f:
                                                f.write(raw)
                                            data_to_echo = raw
                                        else:
                                            # Raw PCM → wrap in a WAV header for easy playback
                                            rate = int(
                                                os.getenv("DG_RAW_SAMPLE_RATE", "48000")
                                            )
                                            ch = int(os.getenv("DG_RAW_CHANNELS", "1"))
                                            wav = _wav_with_header(
                                                raw,
                                                sample_rate=rate,
                                                channels=ch,
                                                bits_per_sample=16,
                                            )
                                            out_path = os.path.join(
                                                base_dir, f"mic_{sid}_{turn_id}.wav"
                                            )
                                            mime = "audio/wav"
                                            with open(out_path, "wb") as f:
                                                f.write(wav)
                                            data_to_echo = wav

                                        _jlog(
                                            "mic_capture_saved",
                                            sid=sid,
                                            turn_id=turn_id,
                                            path=out_path,
                                            bytes=len(raw),
                                            mime=mime,
                                        )

                                        if MIC_ECHO_WS:
                                            # Send the audio back over WS so the client can play it
                                            await _ws_send_diagnostic_audio(
                                                send, turn_id, mime, data_to_echo
                                            )
                                except Exception as e:
                                    _jlog(
                                        "mic_capture_fail",
                                        sid=sid,
                                        err=type(e).__name__,
                                    )

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
        with contextlib.suppress(Exception):
            _cancel_no_audio_watch()
        await _remove_active_ws_entry("cleanup")
        duration = max(0.0, time.time() - start_ts) if start_ts is not None else None
        with contextlib.suppress(Exception):
            _jlog(
                "ws_conn_cleanup",
                conn_id=conn_id,
                sid=sid,
                duration=duration,
                had_disconnect=had_disconnect,
            )
        with contextlib.suppress(Exception):
            if _admin_emit:
                _admin_emit(
                    "ws_conn_cleanup",
                    conn_id=conn_id,
                    sid=sid,
                    duration=duration,
                    had_disconnect=had_disconnect,
                )
        # Clean up safely; never raise in cleanup
        with contextlib.suppress(Exception):
            if rx_task:
                rx_task.cancel()
                await rx_task
        with contextlib.suppress(Exception):
            if dg is not None:
                await dg.close(wait_for_final=False)
                try:
                    set_asr_stream_open(sid, False)
                except Exception:
                    pass
        with contextlib.suppress(Exception):
            bus_task.cancel()
            await bus_task
        with contextlib.suppress(Exception):
            ping_task.cancel()
            await ping_task
        with contextlib.suppress(Exception):
            await send(
                {"type": "websocket.close", "code": 1000, "reason": "normal_shutdown"}
            )


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
        await _pump_bus_to_client(
            sid, lambda msg: websocket.send_text(msg.get("text") or "")
        )
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            await websocket.close(code=1000, reason="normal_shutdown")
            await asyncio.sleep(0.05)
