# app/ws/ws_asgi.py — Phase 2+ (Deepgram wired; WS protocol + delegation; WS-only greet + typed turns)
from __future__ import annotations
import asyncio, os, contextlib, time, io, struct, base64, uuid, copy, json, hashlib, logging
try:
    import audioop  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    audioop = None  # type: ignore[assignment]
from functools import partial
from typing import Optional, Dict, Any, Deque, Callable, Awaitable, List, Tuple, Set
from collections import deque, defaultdict
from app.services.audio.container_sniffer import (
    AudioContainerSniffer,
    coerce_detection_from_meta,
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
from app.config import load_settings
from app.security.ws_token import verify as verify_ws_token
from app.db import db
from app.policy.loader import load_policy, load_policy_layers
from app.policy.interaction_policy import (
    for_idle as interaction_policy_idle,
    for_tts as interaction_policy_tts,
)
from app.services.greet_idempotency import clear_greet_turn_cache
from app.metrics import ws_metrics
from app.flow.emit import add_batch, emit as flow_emit
from app.session_state import set_phase, should_emit_phase

# NEW: invoke LLM on final transcript
from app.services.streaming import (
    run_ws_user_turn,
    prepare_turn_metadata,
    note_turn_commit_latency,
    emit_watchdog_user_end,
)  # NEW
from app.nlu.universal_interpreter import ensure_all_fields as _ensure_universal_fields
from app.ws.barge import BargeState
from app.ws.confirm_window import ConfirmWindow
from app.ws.bus import bus
from app.obs.source_tags import (
    FLOW_SCHEMA_VERSION,
    gate_snapshot,
    make_source_meta,
    ms_since,
    resolve_barge_origin,
)
from app.services.tts.flow_wrapper import make_tts_mask_meta


logger = logging.getLogger(__name__)

# Optional admin emitter
try:
    from app.api_v1.admin import _emit as _admin_emit_base
except Exception:
    _admin_emit_base = None


def _broadcast_admin_frame(event: str, payload: Dict[str, Any]) -> None:
    """Mirror admin diagnostic breadcrumbs onto the session bus."""

    try:
        sid = payload.get("session_id") or payload.get("sid")
    except Exception:
        sid = None

    if not sid:
        return

    try:
        sid_str = str(sid)
    except Exception:
        sid_str = sid  # type: ignore[assignment]

    frame: Dict[str, Any] = {"type": event}
    try:
        frame.update(dict(payload))
    except Exception:
        frame.update(payload)  # type: ignore[arg-type]

    frame.setdefault("session_id", sid_str)
    frame.setdefault("sid", sid_str)

    try:
        bus.broadcast(sid_str, frame)
    except Exception:
        pass


def _admin_emit(event: str, **payload: Any) -> None:
    if callable(_admin_emit_base):
        try:
            _admin_emit_base(event, **payload)
        except Exception:
            pass

    _broadcast_admin_frame(event, payload)


def _with_ws_component(meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if isinstance(meta, dict):
        payload.update(meta)
    elif meta is not None:
        try:
            payload.update(dict(meta))  # type: ignore[arg-type]
        except Exception:
            pass
    payload.setdefault("component", "ws_server")
    return payload


def _current_assistant_turn_id(sid: str) -> Optional[str]:
    try:
        turn = bus.current_assistant_turn(sid)
    except Exception:
        return None
    if turn is None:
        return None
    try:
        return str(turn)
    except Exception:
        return None

def _lookup_tts_state(sid: str, turn_id: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        tts_tbl = db.memory.get("tts_status") or {}
        session_tbl = tts_tbl.get(sid) or {}
    except Exception:
        return None, None

    keys_to_try = []
    if turn_id:
        keys_to_try.append(str(turn_id))
    else:
        keys_to_try.append("greet")

    # Fallback: consider any active (not done) entry
    for key, value in session_tbl.items():
        if key not in keys_to_try and isinstance(value, dict):
            if not value.get("done") and (value.get("started") or value.get("first_chunk")):
                keys_to_try.append(key)

    for key in keys_to_try:
        state = session_tbl.get(key)
        if isinstance(state, dict):
            return dict(state), key

    return None, None


def _is_tts_active(state: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("error"):
        return False
    if state.get("done"):
        return False
    return bool(state.get("started") or state.get("first_chunk"))


def _emit_barge_admin_events(sid: str,
                              phase: Optional[str],
                              previous_phase: Optional[str]) -> None:
    if not phase:
        return
    admin_cb = globals().get("_admin_emit")
    if not callable(admin_cb):
        return

    turn_id = _current_assistant_turn_id(sid)
    tts_state, tts_key = _lookup_tts_state(sid, turn_id)

    payload: Dict[str, Any] = {
        "session_id": sid,
        "sid": sid,
        "phase": phase,
    }
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if previous_phase:
        payload["previous_phase"] = previous_phase
    if tts_key:
        payload["tts_turn_key"] = tts_key
    if tts_state is not None:
        payload["tts_state"] = tts_state

    state = _BARGE_EVENT_STATE.get(sid, {})
    manual_gate = bool(state.get("manual_gate"))
    client_vad_active = bool(state.get("client_vad_active"))
    policy_triggered = bool(state.get("policy_triggered"))
    last_conf = state.get("last_confident_conf")
    if last_conf is None:
        last_conf = state.get("last_partial_conf")
    try:
        last_conf = float(last_conf) if last_conf is not None else None
    except (TypeError, ValueError):
        last_conf = None
    payload.update(
        make_source_meta(
            resolve_barge_origin(
                manual_gate=manual_gate,
                client_vad_recent_ms=ms_since(state.get("client_vad_start_ms")),
                asr_conf_recent=last_conf,
                policy_triggered=policy_triggered,
            ),
            gates=gate_snapshot(
                _is_tts_active(tts_state),
                manual_gate,
                client_vad_active,
            ),
            evidence={
                "ms_since_last_ptt": ms_since(state.get("last_manual_ptt_ms")),
                "ms_since_vad_start": ms_since(state.get("client_vad_start_ms")),
                "dg_conf": last_conf,
            },
        )
    )

    if phase == "paused" and previous_phase != "paused":
        admin_cb("barge_in", **payload)
        if _is_tts_active(tts_state):
            admin_cb("tts_pause", **payload)
    elif phase == "assistant_speaking" and previous_phase == "paused":
        admin_cb("barge_resume", **payload)
        if _is_tts_active(tts_state):
            admin_cb("tts_resume", **payload)
    elif phase == "ready" and previous_phase == "paused":
        admin_cb("barge_commit", **payload)
        if _is_tts_active(tts_state):
            admin_cb("tts_cancel", **payload)

 
_BARGE_EVENT_STATE: Dict[str, Dict[str, Any]] = defaultdict(dict)

ACTIVE_WS: dict[str, dict[str, Any]] = {}
ACTIVE_WS_LOCK = asyncio.Lock()

GREET_SEQ_CACHE: dict[str, Set[int]] = defaultdict(set)
GREET_SEQ_CACHE_LOCK = asyncio.Lock()

WS_ASGI_BUILD = "miccap-v4"  # bump when you redeploy

_SETTINGS = load_settings()
_ADVANCED_LOGGING_ENABLED = bool(
    getattr(_SETTINGS, "advanced_logging_enabled", True)
)

logger.info("server_source_components: enabled")

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


async def _greet_seq_mark_if_new(sid: str, seq: int) -> bool:
    sid_key = str(sid or "") or "default"
    async with GREET_SEQ_CACHE_LOCK:
        seen = GREET_SEQ_CACHE[sid_key]
        if seq in seen:
            return False
        seen.add(seq)
        return True


def _normalize_admin_event_name(name: str) -> str:
    try:
        text = str(name or "").strip().lower()
    except Exception:
        text = ""
    if not text:
        return "log"
    for ch in (" ", "\t", "\r", "\n"):
        text = text.replace(ch, "_")
    text = text.replace("/", ":").replace("..", ".")
    text = text.replace(".", "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text or "log"


def _jlog(event: str, *, admin_event: Optional[str] = None, admin_label: Optional[str] = None, **fields):
    """Legacy logging hook retained for admin diagnostics; no stdout spam."""
    if not _ADVANCED_LOGGING_ENABLED:
        return
    try:
        payload = dict(fields)
        payload.setdefault("event", event)
        payload.setdefault("ts", time.time())

        admin_cb = globals().get("_admin_emit")
        if callable(admin_cb):
            normalized = _normalize_admin_event_name(admin_event or payload.get("event", event))
            admin_payload = dict(payload)
            admin_payload["event"] = normalized
            if admin_label is not None:
                admin_cb(normalized, label=admin_label, **admin_payload)
            else:
                admin_cb(normalized, **admin_payload)
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

    canonical_universal: Dict[str, Any] = {}
    if isinstance(universal, dict):
        try:
            canonical_universal = _ensure_universal_fields(universal)
        except Exception:
            canonical_universal = dict(universal)

    universal_defaults = _ensure_universal_fields({})

    def _resolved_value(key: str, default: Any = None) -> Any:
        for source in (canonical_universal, dialog_nlu):
            if isinstance(source, dict) and key in source:
                return source.get(key)
        return universal_defaults.get(key, default)

    missing_value = _resolved_value("missing", universal_defaults.get("missing", []))
    if isinstance(missing_value, list):
        missing_payload = _safe_copy(missing_value)
    elif missing_value is None:
        missing_payload = []
    else:
        try:
            missing_payload = list(missing_value)
        except Exception:
            missing_payload = [missing_value]

    payload = {
        "event": "nlu",
        "intent": dialog_nlu.get("intent"),
        "confidence": dialog_nlu.get("confidence"),
        "slots": slots_dict,
        "text": text,
        "session_id": sid,
        "needs_clarification": bool(_resolved_value("needs_clarification", False)),
        "missing": missing_payload,
        "phase": _resolved_value("phase"),
        "depth": _resolved_value("depth"),
        "delivery_pref": _resolved_value("delivery_pref"),
        "intent_hint": _resolved_value("intent_hint"),
    }
    if canonical_universal:
        payload["universal"] = _safe_copy(canonical_universal)

    entities_payload: Dict[str, Any] = {}
    universal_entities = canonical_universal.get("entities") if isinstance(canonical_universal, dict) else None
    if isinstance(universal_entities, dict):
        try:
            entities_payload.update(_safe_copy(universal_entities))
        except Exception:
            entities_payload.update(universal_entities)

    dialog_entities = dialog_nlu.get("entities") if isinstance(dialog_nlu, dict) else None
    if isinstance(dialog_entities, dict):
        for key, value in dialog_entities.items():
            entities_payload[key] = _safe_copy(value)

    dialog_products = dialog_nlu.get("products") if isinstance(dialog_nlu, dict) else None
    if isinstance(dialog_products, list):
        entities_payload["products"] = list(dialog_products)
        if dialog_products:
            entities_payload.setdefault("product", dialog_products[0])

    if "products" not in entities_payload:
        entities_payload["products"] = []

    if "product" not in entities_payload:
        products = entities_payload.get("products")
        if isinstance(products, list) and products:
            entities_payload["product"] = products[0]
        else:
            entities_payload["product"] = None

    if "env" not in entities_payload:
        env_candidate = None
        if isinstance(dialog_entities, dict):
            env_candidate = dialog_entities.get("env")
        if env_candidate is None and isinstance(dialog_nlu, dict):
            env_candidate = dialog_nlu.get("env")
        entities_payload["env"] = env_candidate

    if "keywords" not in entities_payload:
        keywords = canonical_universal.get("entities", {}).get("keywords") if isinstance(canonical_universal.get("entities"), dict) else None
        entities_payload["keywords"] = list(keywords or [])

    payload["entities"] = _safe_copy(entities_payload)

    evidence_payload: Dict[str, Any] = {}
    intent_val = dialog_nlu.get("intent") if isinstance(dialog_nlu, dict) else None
    if intent_val:
        evidence_payload["intent"] = intent_val
    confidence_val = dialog_nlu.get("confidence") if isinstance(dialog_nlu, dict) else None
    if confidence_val is not None:
        try:
            evidence_payload["confidence"] = float(confidence_val)
        except Exception:
            try:
                evidence_payload["confidence"] = str(confidence_val)
            except Exception:
                pass
    phase_val = payload.get("phase")
    if isinstance(phase_val, str) and phase_val:
        evidence_payload["phase"] = phase_val
    try:
        payload.update(
            make_source_meta(
                "nlu_runtime",
                evidence=evidence_payload or None,
            )
        )
    except Exception:
        pass

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

_VOICE_METRICS_SUBSCRIBERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}
_VOICE_METRICS_QUEUE = None
_VOICE_METRICS_TASK: Optional[asyncio.Task] = None
_VOICE_METRICS_LOCK: Optional[asyncio.Lock] = None


def _get_voice_metrics_lock() -> asyncio.Lock:
    global _VOICE_METRICS_LOCK
    lock = _VOICE_METRICS_LOCK
    if lock is None:
        lock = asyncio.Lock()
        _VOICE_METRICS_LOCK = lock
    return lock


async def _voice_metrics_pump_loop(queue) -> None:
    from queue import Empty

    global _VOICE_METRICS_QUEUE, _VOICE_METRICS_TASK

    try:
        while True:
            try:
                frame = queue.get(timeout=0.2)
            except Empty:
                await asyncio.sleep(0.05)
                continue

            if not isinstance(frame, dict):
                continue

            payload = {k: v for k, v in frame.items() if k != "type"}

            lock = _get_voice_metrics_lock()
            async with lock:
                subscribers = list(_VOICE_METRICS_SUBSCRIBERS.items())

            if not subscribers:
                continue

            to_remove: List[str] = []
            for key, sender in subscribers:
                message = {"type": "asr.metrics", "payload": dict(payload)}
                try:
                    await sender(message)
                except Exception:
                    to_remove.append(key)

            if to_remove:
                lock = _get_voice_metrics_lock()
                async with lock:
                    for key in to_remove:
                        _VOICE_METRICS_SUBSCRIBERS.pop(key, None)
    except asyncio.CancelledError:
        pass
    finally:
        with contextlib.suppress(Exception):
            if hasattr(bus, "unsubscribe"):
                bus.unsubscribe("asr.metrics", queue)
        if _VOICE_METRICS_QUEUE is queue:
            _VOICE_METRICS_QUEUE = None
        if _VOICE_METRICS_TASK is not None and _VOICE_METRICS_TASK.done():
            _VOICE_METRICS_TASK = None


async def _ensure_voice_metrics_pump() -> None:
    global _VOICE_METRICS_QUEUE, _VOICE_METRICS_TASK

    lock = _get_voice_metrics_lock()
    async with lock:
        task = _VOICE_METRICS_TASK
        if task is not None and not task.done():
            return
        if task is not None and task.done():
            with contextlib.suppress(Exception):
                task.result()
            _VOICE_METRICS_TASK = None

        queue = _VOICE_METRICS_QUEUE
        if queue is None:
            queue = bus.subscribe("asr.metrics")
            _VOICE_METRICS_QUEUE = queue

        loop = asyncio.get_running_loop()
        _VOICE_METRICS_TASK = loop.create_task(_voice_metrics_pump_loop(queue))


async def _register_voice_metrics_subscriber(conn_id: str, send) -> bool:
    try:
        await _ensure_voice_metrics_pump()
    except Exception:
        return False

    lock = _get_voice_metrics_lock()
    async with lock:
        _VOICE_METRICS_SUBSCRIBERS[conn_id] = partial(_ws_send_json, send)
    return True

async def _unregister_voice_metrics_subscriber(conn_id: str) -> None:
    lock = _get_voice_metrics_lock()
    async with lock:
        _VOICE_METRICS_SUBSCRIBERS.pop(conn_id, None)


async def _pump_bus_to_client(
    sid: str,
    send,
    on_frame: Optional[Callable[[Dict[str, Any]], None]] = None,
):
    """Forward frames from StreamBus to the WS client as JSON, suppressing duplicate assistant finals."""
    import json as _json
    import asyncio
    import contextlib
    from queue import Empty
    from app.ws.bus import bus

    # Session-scoped guard: only one assistant final per turn_id
    assistant_finals_sent: set[int] = set()

    q = bus.subscribe(sid)
    try:
        while True:
            try:
                fr = q.get(timeout=0.05)  # 'fr' is already a dict from the bus
            except Empty:
                await asyncio.sleep(0.01)
                continue

            if callable(on_frame):
                with contextlib.suppress(Exception):
                    on_frame(fr)            

            # --- Duplicate assistant-final suppression ---
            try:
                msg_type = (fr.get("type") or "").lower()
                is_results = msg_type == "results"
                role_like = (fr.get("role") or fr.get("source") or fr.get("speaker") or "").lower()
                is_assistant = role_like == "assistant"
                is_final = bool(fr.get("is_final"))
                turn_id = fr.get("turn_id")
            except Exception:
                is_results = False
                is_assistant = False
                is_final = False
                turn_id = None

            if is_results and is_assistant and is_final and isinstance(turn_id, int):
                if turn_id in assistant_finals_sent:
                    with contextlib.suppress(Exception):
                        # Optional: if your bus has a logger
                        bus.log("dup_answer_suppressed", sid=sid, turn_id=turn_id)  # type: ignore[attr-defined]
                    # Skip forwarding this duplicate final
                    await asyncio.sleep(0)  # yield control
                    continue
                assistant_finals_sent.add(turn_id)
                with contextlib.suppress(Exception):
                    bus.log("assistant_final_forwarded", sid=sid, turn_id=turn_id)  # type: ignore[attr-defined]
            # --- /Duplicate assistant-final suppression ---

            try:
                await send(
                    {
                        "type": "websocket.send",
                        "text": _json.dumps(fr, separators=(",", ":"), ensure_ascii=False),
                    }
                )
            except Exception:
                await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        pass
    finally:
        with contextlib.suppress(Exception):
            if hasattr(bus, "unsubscribe"):
                bus.unsubscribe(sid, q)

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
    turn_timing: Optional[Dict[str, List[float]]] = None,
    on_turn_finish: Optional[Callable[[int, str, bool, int], None]] = None,
    on_asr_partial: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_evidence: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    final_guard_hooks: Optional[Dict[str, Any]] = None,
    on_transport_error: Optional[Callable[[str], None]] = None,
):
    """Relay Deepgram events to client and, on final, kick LLM turn."""
    cfg_ref = getattr(dg, "_cfg", None)
    if not isinstance(cfg_ref, dict):
        cfg_ref = getattr(dg, "cfg", {}) or {}
    else:
        cfg_ref = cfg_ref or {}
    close_reason_payload: Dict[str, Any] = {"reason": "loop_exit"}
    try:
        guard_env_raw = os.getenv("WS_FINAL_GUARD_MS", "1500")
        guard_env_ms = int(guard_env_raw)
    except Exception:
        guard_env_ms = 1500
    guard_env_ms = max(0, guard_env_ms)
    try:
        effective_guard = int(str(cfg_ref.get("_effective_utterance_end_ms") or 0))
    except Exception:
        effective_guard = 0
    guard_ms = max(guard_env_ms, max(0, effective_guard))
    guard_state: Dict[str, Any] = {
        "guard_ms": guard_ms,
        "pending": None,
        "pending_ts": 0.0,
        "task": None,
        "last_audio_ts": 0.0,
        "local_vad_event": asyncio.Event(),
        "local_vad_seen": False,
    }
    guard_state["local_vad_event"].set()
    guard_requires_vad = guard_state["guard_ms"] > 0
    reset_ref = None
    vad_ref = None
    if isinstance(final_guard_hooks, dict):
        reset_ref = final_guard_hooks.get("reset_ref")
        vad_ref = final_guard_hooks.get("local_vad_ref")
    _jlog(
        "ws_final_guard_config",
        sid=sid,
        guard_ms=guard_state["guard_ms"],
        env_ms=guard_env_ms,
        effective_ms=effective_guard,
    )

async def _emit_user_final_payload(
    turn_id_for_event: int, text: str, *, final_reason: str = "provider_final"
) -> None:
    final_seen[0] = True
    pending_final_texts.pop(turn_id_for_event, None)
    now_ts = time.time()

    if turn_timing is not None:
        with contextlib.suppress(Exception):
            turn_timing.setdefault("final", [0.0])[0] = now_ts
            if text:
                first_holder = turn_timing.setdefault("first_partial", [0.0])
                if not first_holder[0]:
                    first_holder[0] = now_ts

    # --- Null-turn suppression: drop empty/noisy finals ---
    text_str = (text or "").strip()
    voiced_ms = 0
    with contextlib.suppress(Exception):
        if isinstance(turn_timing, dict):
            voiced_ms = int(turn_timing.get("voiced_ms", [0])[0] or 0)

    min_chars = int(os.getenv("NULL_TURN_MIN_CHARS", "5"))
    min_voiced = int(os.getenv("NULL_TURN_MIN_VOICED_MS", "600"))

    if len(text_str) < min_chars and voiced_ms < min_voiced:
        _jlog(
            "null_turn_suppressed",
            sid=sid,
            turn_id=turn_id_for_event,
            voiced_ms=voiced_ms,
            text_len=len(text_str),
            final_reason=final_reason,
        )
        with contextlib.suppress(Exception):
            await _ws_send_json(send, make_utterance_end(turn_id_for_event))
        return
    # --- /Null-turn suppression ---

    await _ws_send_json(
        send,
        make_results(
            turn_id_for_event,
            transcript=text_str,
            confidence=0.0,
            is_final=True,
        ),
    )

    if on_turn_finish:
        with contextlib.suppress(Exception):
            on_turn_finish(turn_id_for_event, final_reason, False, len(text_str))

    await _ws_send_json(send, make_utterance_end(turn_id_for_event))

    with contextlib.suppress(Exception):
        if _admin_emit:
            final_payload: Dict[str, Any] = {
                "session_id": sid,
                "turn_id": turn_id_for_event,
            }
            if text_str:
                final_payload["text"] = text_str
                final_payload["text_preview"] = _clip_text(text_str)
            _admin_emit("asr:final", **final_payload)

    if not text_str:
        return

    dialog_nlu_pre: Dict[str, Any] = {}
    universal_pre: Dict[str, Any] = {}
    meta_stub = {"source": "user_ws", "channel": "ws"}
    try:
        prepared_meta, dialog_nlu_raw, _ = prepare_turn_metadata(text, dict(meta_stub))
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
        return
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
                    make_error("llm_turn_fail", e.__class__.__name__),
                )

    asyncio.create_task(_bg_turn())

    async def _guard_runner(payload: Tuple[int, str, str]) -> None:
        turn_id_for_event, text, final_reason = payload
        try:
            guard_wait_ms = guard_state["guard_ms"]
            while guard_wait_ms > 0:
                base_ts = guard_state["last_audio_ts"] or guard_state["pending_ts"]
                if base_ts <= 0:
                    base_ts = guard_state["pending_ts"]
                elapsed_ms = max(0.0, (time.time() - base_ts) * 1000.0)
                remaining = guard_wait_ms - elapsed_ms
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.2, remaining / 1000.0))
                guard_wait_ms = guard_state["guard_ms"]
                if guard_state["pending"] is not payload:
                    return
            if guard_requires_vad:
                await guard_state["local_vad_event"].wait()
            if guard_state["pending"] is payload:
                guard_state["pending"] = None
                pending_final_texts.pop(turn_id_for_event, None)
                _jlog(
                    "ws_final_guard_emit",
                    sid=sid,
                    turn_id=turn_id_for_event,
                    guard_ms=guard_state["guard_ms"],
                )
                await _emit_user_final_payload(turn_id_for_event, text, final_reason=final_reason)
        except asyncio.CancelledError:
            raise
        finally:
            guard_state["task"] = None

    def _start_guard_task(payload: Tuple[int, str, str]) -> None:
        task = guard_state.get("task")
        if task and not task.done():
            task.cancel()
        guard_state["task"] = asyncio.create_task(_guard_runner(payload))

    def _schedule_guarded_final(
        turn_id_for_event: int, text: str, *, final_reason: str = "provider_final"
    ) -> None:
        payload = (turn_id_for_event, text, final_reason)
        guard_state["pending"] = payload
        guard_state["pending_ts"] = time.time()
        if guard_state["last_audio_ts"] <= 0:
            guard_state["last_audio_ts"] = guard_state["pending_ts"]
        pending_final_texts[turn_id_for_event] = text
        _jlog(
            "ws_final_guard_schedule",
            sid=sid,
            turn_id=turn_id_for_event,
            guard_ms=guard_state["guard_ms"],
            local_vad_active=guard_state["local_vad_seen"],
        )
        _start_guard_task(payload)

    def _guard_reset_external(reason: str = "audio") -> None:
        guard_state["last_audio_ts"] = time.time()
        payload = guard_state.get("pending")
        if not payload:
            return
        turn_id_for_event, _, _ = payload
        _jlog(
            "ws_final_guard_reset",
            sid=sid,
            turn_id=turn_id_for_event,
            reason=reason,
        )
        _start_guard_task(payload)

    def _guard_note_local_vad(signal: str) -> None:
        sig = (signal or "").strip().lower()
        if sig in {"start", "begin", "active"}:
            guard_state["local_vad_seen"] = True
            guard_state["local_vad_event"].clear()
            payload = guard_state.get("pending")
            if payload:
                _jlog(
                    "ws_final_guard_local_vad",
                    sid=sid,
                    turn_id=payload[0],
                    action="start",
                )
        elif sig in {"stop", "end", "inactive"}:
            if guard_state["local_vad_seen"]:
                guard_state["local_vad_event"].set()
            guard_state["local_vad_seen"] = False
            payload = guard_state.get("pending")
            if payload:
                _jlog(
                    "ws_final_guard_local_vad",
                    sid=sid,
                    turn_id=payload[0],
                    action="stop",
                )

    if isinstance(reset_ref, list) and reset_ref:
        reset_ref[0] = _guard_reset_external
    if isinstance(vad_ref, list) and vad_ref:
        vad_ref[0] = _guard_note_local_vad

    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                delta_ms = None
                now_ts = time.time()
                if turn_timing is not None:
                    with contextlib.suppress(Exception):
                        holder = turn_timing.setdefault("dg_open", [0.0])
                        holder[0] = now_ts
                        start_ts = turn_timing.get("start", [0.0])[0]
                        if start_ts:
                            delta_ms = int((now_ts - start_ts) * 1000)
                _jlog(
                    "dg_asr_open",
                    sid=sid,
                    turn_id=turn_id_ref[0],
                    delta_from_turn_ms=delta_ms,
                )
                if on_evidence:
                    with contextlib.suppress(Exception):
                        on_evidence("open", dict(ev))
                with contextlib.suppress(Exception):
                    if asr_ready_evt and not asr_ready_evt.is_set():
                        asr_ready_evt.set()
                with contextlib.suppress(Exception):
                    if _admin_emit:
                        turn_for_event = turn_id_ref[0] or None
                        _admin_emit(
                            "asr:start",
                            session_id=sid,
                            turn_id=turn_for_event,
                        )
                if on_asr_open_flush:
                    with contextlib.suppress(Exception):
                        await on_asr_open_flush()
                        await asyncio.sleep(0.05)
                        await on_asr_open_flush()
                continue

            if et in ("user_partial", "user_final"):
                is_final = et == "user_final"
                text = (ev.get("text") or "").strip()
                if not is_final and text:
                    last_partial_text[0] = text
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
                    last_user_final_text[0] = text
                    if text:
                        last_partial_text[0] = text
                else:
                    allow_barge, _, _, _ = _decide_barge_attempt("vad")
                    if not allow_barge:
                        continue
                    with contextlib.suppress(Exception):
                        asr_seen_partial[0] = True
                    if turn_timing is not None:
                        with contextlib.suppress(Exception):
                            first_holder = turn_timing.setdefault("first_partial", [0.0])
                            if not first_holder[0]:
                                first_holder[0] = time.time()
                if (not is_final) and on_asr_partial:
                    with contextlib.suppress(Exception):
                        on_asr_partial(ev)

                preview_text = _clip_text(text)
                if on_evidence:
                    with contextlib.suppress(Exception):
                        on_evidence("final" if is_final else "partial", dict(ev))
                _jlog(
                    "dg_transcript",
                    sid=sid,
                    turn_id=turn_id_for_event,
                    is_final=is_final,
                    chars=len(text),
                    preview=preview_text,
                    ts_ms=int(time.time() * 1000),
                )
                if not is_final:
                    with contextlib.suppress(Exception):
                        if _admin_emit:
                            asr_partial_counter[0] += 1
                            emit_partial = (
                                asr_partial_counter[0] == 1
                                or asr_partial_counter[0] % 5 == 0
                            )
                            if emit_partial:
                                _admin_emit(
                                    "asr:partial",
                                    session_id=sid,
                                    turn_id=turn_id_for_event,
                                    text_preview=preview_text,
                                )

                if not is_final:
                    await _ws_send_json(
                        send,
                        make_results(
                            turn_id_for_event,
                            transcript=text,
                            confidence=0.0,
                            is_final=False,
                        ),
                    )
                    continue

                final_seen[0] = True
                if text and turn_timing is not None:
                    with contextlib.suppress(Exception):
                        first_holder = turn_timing.setdefault("first_partial", [0.0])
                        if not first_holder[0]:
                            first_holder[0] = time.time()
                _schedule_guarded_final(turn_id_for_event, text)
                continue

            if et == "asr_error":
                err = _clip_text(str(ev.get("error") or "unknown"), 160)
                _jlog("dg_asr_error", sid=sid, turn_id=turn_id_ref[0], error=err)
                close_reason_payload = {"reason": "error", "error": err}
                if on_evidence:
                    with contextlib.suppress(Exception):
                        on_evidence("error", {"error": err})
                with contextlib.suppress(Exception):
                    if _admin_emit:
                        _admin_emit(
                            "asr:error",
                            session_id=sid,
                            turn_id=turn_id_ref[0],
                            msg=err,
                            error=err,
                        )
                await _ws_send_json(send, make_error("asr_error", err))
    except asyncio.CancelledError:
        close_reason_payload = {"reason": "cancelled"}
        return
    except Exception as e:
        close_reason_payload = {"reason": "error", "error": e.__class__.__name__}
        with contextlib.suppress(Exception):
            await _ws_send_json(send, make_error("relay_fail", e.__class__.__name__))
        if callable(on_transport_error):
            with contextlib.suppress(Exception):
                on_transport_error("relay_fail")
    finally:
        if on_evidence:
            with contextlib.suppress(Exception):
                payload = dict(close_reason_payload)
                if (
                    payload.get("reason") == "loop_exit"
                    and asr_evidence_ref[0].get("final_received")
                ):
                    payload["reason"] = "normal"
                on_evidence("close", payload)
        task = guard_state.get("task")
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if isinstance(reset_ref, list) and reset_ref:
            reset_ref[0] = None
        if isinstance(vad_ref, list) and vad_ref:
            vad_ref[0] = None

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
    ws_open_ts = start_ts
    last_msg_ts = start_ts
    had_disconnect = False
    active_ws_registered = False
    active_ws_closed = False
    _jlog("mic_capture_cfg", sid=sid, enabled=MIC_CAPTURE, echo_ws=MIC_ECHO_WS)

    flow_session_open_emitted = False
    flow_session_ready_emitted = False
    flow_session_close_emitted = False
    flow_session_config_emitted = False
    session_flow_event_id: List[Optional[str]] = [None]
    ws_transport_parent_id: List[Optional[str]] = [None]
    post_greet_phase_active = [False]
    flow_turn_commit_emitted: Set[int] = set()
    flow_turn_abort_emitted: Set[int] = set()
    greet_end_pending = False
    greet_end_emitted = False

    def _emit_flow_event(
        type_: str,
        *,
        phase: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload = _with_ws_component(meta)
        try:
            return (
                flow_emit(
                    session_id=sid,
                    level="flow",
                    phase=phase,
                    type=type_,
                    who="system",
                    meta=payload,
                )
                or ""
            )
        except Exception:
            return ""

    def _emit_debug_event(
        type_: str,
        *,
        phase: str,
        parent_id: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not parent_id:
            return ""
        payload = _with_ws_component(meta)
        try:
            return (
                flow_emit(
                    session_id=sid,
                    level="debug",
                    phase=phase,
                    type=type_,
                    who="system",
                    meta=payload,
                    parent_id=parent_id,
                )
                or ""
            )
        except Exception:
            return ""

    def _ensure_ws_parent_id() -> Optional[str]:
        if ws_transport_parent_id[0] is None:
            try:
                ws_transport_parent_id[0] = (
                    flow_emit(
                        session_id=sid,
                        type="ws_frames",
                        phase="transport",
                        who="server",
                        meta=_with_ws_component({"dir": "pump_start"}),
                    )
                    or None
                )
            except Exception:
                ws_transport_parent_id[0] = None
            if ws_transport_parent_id[0] is None and session_flow_event_id[0]:
                ws_transport_parent_id[0] = session_flow_event_id[0]
        return ws_transport_parent_id[0]

    policy: Dict[str, Any] = {}
    policy_version: str = "unknown"
    current_interaction_policy_mode: List[Optional[str]] = [None]

    def _push_interaction_policy(snapshot: Dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            return
        payload = dict(snapshot)
        mode_value = payload.get("mode")
        try:
            mode_str = str(mode_value) if mode_value is not None else ""
        except Exception:
            mode_str = ""
        if current_interaction_policy_mode[0] == mode_str:
            pass
        else:
            current_interaction_policy_mode[0] = mode_str
        frame = {
            "type": "policy.interaction",
            "mode": mode_str or None,
            "scope": "interaction",
            "policy": payload,
        }
        try:
            asyncio.create_task(_ws_send_json(send, frame))
        except Exception:
            pass
        with contextlib.suppress(Exception):
            _admin_emit("policy:applied", session_id=sid, mode=mode_str or None, policy=payload)

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
    selected_subprotocol = "bearer"
    try:
        for _sp in scope.get("subprotocols") or []:
            if not isinstance(_sp, str):
                continue
            if _sp.startswith("bearer."):
                token = _sp.split(".", 1)[1].strip()
                selected_subprotocol = _sp
                break
            if _sp == "bearer":
                selected_subprotocol = _sp
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

    await send({"type": "websocket.accept", "subprotocol": selected_subprotocol})

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

    try:
        policy_layers = load_policy_layers(session_id=sid)
    except Exception:
        policy_layers = None

    if isinstance(policy_layers, dict):
        candidate_policy = policy_layers.get("effective_policy")
        if isinstance(candidate_policy, dict):
            policy = candidate_policy
        candidate_version = policy_layers.get("policy_version")
        if candidate_version is not None:
            try:
                policy_version = str(candidate_version)
            except Exception:
                policy_version = "unknown"

    if not policy:
        try:
            policy = load_policy()
        except Exception:
            policy = {}

    if not isinstance(policy_version, str) or not policy_version:
        policy_version = "unknown"

    manual_feature_enabled = True
    manual_mode_manual_only = False
    auto_commit_when_ready = True
    local_vad_allowed = True
    barge_require_asr_evidence = False
    barge_suppress_mode = "none"
    barge_post_tts_hold_ms = 0
    barge_allow_ptt = True
    auto_commit_requires_dual = False
    auto_commit_requires_asr_ready = False

    def _interaction_policy_idle_snapshot() -> Dict[str, Any]:
        allow_ptt = bool(manual_feature_enabled and barge_allow_ptt)
        allow_auto = bool(local_vad_allowed and not manual_mode_manual_only)
        return interaction_policy_idle(
            auto_commit_when_ready=bool(auto_commit_when_ready),
            allow_ptt_barge=allow_ptt,
            allow_auto_vad=allow_auto,
        )

    def _interaction_policy_tts_snapshot() -> Dict[str, Any]:
        allow_ptt = bool(manual_feature_enabled and barge_allow_ptt)
        return interaction_policy_tts(
            auto_commit_when_ready=bool(auto_commit_when_ready),
            allow_ptt_barge=allow_ptt,
        )

    def _reapply_interaction_policy() -> None:
        mode = current_interaction_policy_mode[0] or ""
        if mode == "manual_only_during_tts":
            _push_interaction_policy(_interaction_policy_tts_snapshot())
        else:
            _push_interaction_policy(_interaction_policy_idle_snapshot())

    if not flow_session_open_emitted:
        meta = {"policy_version": policy_version}
        try:
            meta["source_meta"] = make_source_meta(
                "policy_loader",
                evidence={"policy_version": policy_version} if policy_version else None,
            )
        except Exception:
            pass
        session_event_id = _emit_flow_event("session_open", phase="session", meta=meta)
        flow_session_open_emitted = True
        if session_event_id:
            session_flow_event_id[0] = session_event_id
            policy_meta: Dict[str, Any] = {"policy_version": policy_version}
            if isinstance(policy, dict):
                try:
                    policy_meta["policy"] = copy.deepcopy(policy)
                except Exception:
                    try:
                        policy_meta["policy"] = dict(policy)
                    except Exception:
                        policy_meta["policy"] = {}
            try:
                policy_meta["source_meta"] = make_source_meta(
                    "policy_loader",
                    evidence={"policy_version": policy_version} if policy_version else None,
                )
            except Exception:
                pass
            _emit_debug_event(
                "policy_snapshot",
                phase="session",
                parent_id=session_event_id,
                meta=policy_meta,
            )
            runtime_meta = {
                "manual_feature_enabled": bool(manual_feature_enabled),
                "manual_mode_manual_only": bool(manual_mode_manual_only),
                "auto_commit_when_ready": bool(auto_commit_when_ready),
                "local_vad_allowed": bool(local_vad_allowed),
                "barge_require_asr_evidence": bool(barge_require_asr_evidence),
                "barge_suppress_mode": barge_suppress_mode or "none",
                "barge_post_tts_hold_ms": int(barge_post_tts_hold_ms),
                "auto_commit_requires_dual": bool(auto_commit_requires_dual),
                "auto_commit_requires_asr_ready": bool(auto_commit_requires_asr_ready),
            }
            try:
                runtime_meta["source_meta"] = make_source_meta(
                    "policy_runtime",
                    evidence={
                        "auto_commit_when_ready": bool(auto_commit_when_ready),
                        "auto_commit_requires_dual": bool(auto_commit_requires_dual),
                        "auto_commit_requires_asr_ready": bool(auto_commit_requires_asr_ready),
                    },
                )
            except Exception:
                pass
            _emit_debug_event(
                "runtime_flags",
                phase="session",
                parent_id=session_event_id,
                meta=runtime_meta,
            )

    transport_meta = {
        "path": str(scope.get("path") or ""),
        "client_ip": client_ip,
    }
    transport_event_id = _emit_flow_event(
        "ws_transport",
        phase="session",
        meta=transport_meta,
    )
    if transport_event_id:
        ws_transport_parent_id[0] = transport_event_id
    elif session_flow_event_id[0]:
        ws_transport_parent_id[0] = session_flow_event_id[0]

    ws_frames_in = 0
    ws_bytes_in = 0
    ws_text_frames_in = 0
    ws_frames_out = 0
    ws_bytes_out = 0
    _WS_FRAME_SAMPLE = 20
    backpressure_drop_count = 0
    backpressure_last_emit = 0.0
    backpressure_last_queue_len = 0
    backpressure_emit_interval = 1.0

    _raw_send = send

    async def _send_instrumented(message, *, route: str = "app"):
        nonlocal ws_frames_out, ws_bytes_out

        frame_type = None
        try:
            frame_type = message.get("type") if isinstance(message, dict) else None
        except Exception:
            frame_type = None
        if frame_type == "websocket.send":
            payload_bytes = 0
            frame_kind = "text"
            text_payload = message.get("text") if isinstance(message, dict) else None
            if text_payload is not None:
                try:
                    payload_bytes = len((text_payload or "").encode("utf-8"))
                except Exception:
                    payload_bytes = len(text_payload or "")
            else:
                frame_kind = "binary"
                try:
                    payload_bytes = len(message.get("bytes") or b"") if isinstance(message, dict) else 0
                except Exception:
                    payload_bytes = 0
            ws_frames_out += 1
            ws_bytes_out += payload_bytes
            parent_id = _ensure_ws_parent_id()
            if parent_id and ws_frames_out % _WS_FRAME_SAMPLE == 0:
                _emit_debug_event(
                    "ws_frame_out",
                    phase="session",
                    parent_id=parent_id,
                    meta={
                        "type": frame_kind,
                        "bytes": payload_bytes,
                        "route": route,
                    },
                )
        await _raw_send(message)

    send = _send_instrumented

    async def _safe_close(code: int, reason: str) -> None:
        try:
            flow_emit(
                session_id=sid,
                type="ws_close",
                phase="transport",
                who="server",
                meta=_with_ws_component({"code": code, "reason": reason}),
            )
        except Exception:
            pass
        try:
            await send({"type": "websocket.close", "code": code, "reason": reason})
        except Exception:
            pass

    async def _handle_startup_failure(ex: Exception) -> None:
        if isinstance(ex, asyncio.CancelledError):
            raise
        try:
            flow_emit(
                session_id=sid,
                type="ws_error",
                phase="transport",
                who="server",
                meta=_with_ws_component(
                    {
                    "where": "_ws_chat_asgi_impl",
                    "message": str(ex)[:500],
                    }
                ),
            )
        except Exception:
            pass
        try:
            await _ws_send_json(
                send,
                {
                    "type": "Error",
                    "code": "WS_INIT_FAILED",
                    "message": "server error during WS init",
                },
            )
        except Exception:
            pass
        await _safe_close(1011, "server error")

    with contextlib.suppress(Exception):
        clear_greet_turn_cache(sid)

    bus_task: Optional[asyncio.Task] = None

    def _ensure_bus_task_started() -> None:
        nonlocal bus_task
        if bus_task is not None:
            return

        loop = asyncio.get_running_loop()

        def _start_bus_pump() -> None:
            nonlocal bus_task
            if bus_task is not None:
                return
            bus_task = loop.create_task(
                _pump_bus_to_client(sid, partial(send, route="bus"), _handle_bus_frame)
            )

        loop.call_soon(_start_bus_pump)

    voice_metrics_registered = False
    try:
        voice_metrics_registered = await _register_voice_metrics_subscriber(conn_id, send)
    except Exception:
        voice_metrics_registered = False

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
        _push_interaction_policy(_interaction_policy_idle_snapshot())
    except Exception as ex:
        detail = str(ex).strip() or "initial ready failed"
        with contextlib.suppress(Exception):
            flow_emit(
                session_id=sid,
                type="ws_error",
                phase="transport",
                who="server",
                meta=_with_ws_component(
                    {
                    "where": "initial_ready",
                    "cause": ex.__class__.__name__,
                    "msg": str(ex)[:300],
                    }
                ),
            )
        with contextlib.suppress(Exception):
            await _ws_send_json(
                send,
                {
                    "type": "Error",
                    "code": "INITIAL_READY_FAILED",
                    "detail": detail[:200],
                },
            )
        with contextlib.suppress(Exception):
            await _safe_close(1011, "initial_ready_failed")
        return

    _ensure_bus_task_started()

    cfg: Dict[str, Any] = {"advanced_logging_enabled": _ADVANCED_LOGGING_ENABLED}
    ws_configured = False
    configure_ack_sent = False
    if not isinstance(policy, dict):
        policy = {}
    if isinstance(policy, dict):
        cfg["interaction_policy"] = policy
        with contextlib.suppress(Exception):
            _jlog(
                "EVT_POLICY_LOAD",
                sid=sid,
                keys=len(policy.keys()),
            )
    else:
        policy = {}
    try:
        runtime_cfg = db.get_config()
        if isinstance(runtime_cfg, dict):
            manual_feature_enabled = bool(
                runtime_cfg.get("feature_manual_barge_in", manual_feature_enabled)
            )
            manual_mode_manual_only = bool(
                runtime_cfg.get("barge_in_mode_manual", manual_mode_manual_only)
            )
            auto_commit_when_ready = bool(
                runtime_cfg.get("auto_commit_when_ready", auto_commit_when_ready)
            )
    except Exception:
        pass
    try:
        from app.services import admin_settings as _admin_settings  # type: ignore

        admin_cfg = _admin_settings.get_settings()
        if isinstance(admin_cfg, dict):
            manual_feature_enabled = bool(
                admin_cfg.get("feature_manual_barge_in", manual_feature_enabled)
            )
            manual_mode_manual_only = bool(
                admin_cfg.get("barge_in_mode_manual", manual_mode_manual_only)
            )
            auto_commit_when_ready = bool(
                admin_cfg.get("auto_commit_when_ready", auto_commit_when_ready)
            )
    except Exception:
        pass

    config_manual_feature_enabled = bool(manual_feature_enabled)
    config_auto_commit_enabled = bool(auto_commit_when_ready)
    voice_policy = policy.get("voice_runtime") if isinstance(policy, dict) else {}
    confirm_policy = (
        voice_policy.get("confirm_window") if isinstance(voice_policy, dict) else {}
    )
    confirm_first_policy = (
        confirm_policy.get("first_turn") if isinstance(confirm_policy, dict) else {}
    )
    confirm_warm_policy = (
        confirm_policy.get("warm_turn") if isinstance(confirm_policy, dict) else {}
    )
    snr_policy = voice_policy.get("snr_threshold_db") if isinstance(voice_policy, dict) else {}
    barge_policy = voice_policy.get("barge_in") if isinstance(voice_policy, dict) else {}
    auto_commit_policy = (
        voice_policy.get("auto_commit") if isinstance(voice_policy, dict) else {}
    )

    policy_auto_commit_enabled = True

    if isinstance(auto_commit_policy, dict):
        enabled_value = auto_commit_policy.get("enabled")
        if isinstance(enabled_value, bool):
            policy_auto_commit_enabled = enabled_value
        requires_dual_value = auto_commit_policy.get("requires_dual_evidence")
        if isinstance(requires_dual_value, bool):
            auto_commit_requires_dual = requires_dual_value
        requires_asr_value = auto_commit_policy.get("asr_ready_required")
        if isinstance(requires_asr_value, bool):
            auto_commit_requires_asr_ready = requires_asr_value

    if isinstance(barge_policy, dict):
        allow_ptt_value = barge_policy.get("allow_ptt")
        if isinstance(allow_ptt_value, bool):
            barge_allow_ptt = allow_ptt_value
        allow_local_vad_value = barge_policy.get("allow_local_vad")
        if isinstance(allow_local_vad_value, bool):
            local_vad_allowed = allow_local_vad_value
        require_asr_value = barge_policy.get("require_asr_evidence")
        if isinstance(require_asr_value, bool):
            barge_require_asr_evidence = require_asr_value
        suppress_mode_value = barge_policy.get("suppress_during_tts")
        if isinstance(suppress_mode_value, str):
            suppress_mode_value = suppress_mode_value.strip().lower()
            if suppress_mode_value:
                barge_suppress_mode = suppress_mode_value
        post_hold_value = barge_policy.get("post_tts_hold_ms")
        if isinstance(post_hold_value, (int, float)):
            barge_post_tts_hold_ms = max(0, int(post_hold_value))

    manual_feature_enabled = bool(config_manual_feature_enabled and barge_allow_ptt)
    auto_commit_when_ready = bool(
        config_auto_commit_enabled and policy_auto_commit_enabled
    )

    _reapply_interaction_policy()

    _jlog(
        "voice_runtime_effective",
        sid=sid,
        feature_manual_barge_in=bool(manual_feature_enabled),
        barge_in_mode_manual=bool(manual_mode_manual_only),
        auto_commit_when_ready=bool(auto_commit_when_ready),
    )

    _jlog(
        "voice_runtime_config",
        sid=sid,
        suppress_during_tts=barge_suppress_mode or "none",
        post_tts_hold_ms=int(barge_post_tts_hold_ms),
        allow_ptt=bool(barge_allow_ptt),
    )

    resolved_policy_snapshot = {
        "voice_runtime": {
            "barge_in": {
                "suppress_during_tts": barge_suppress_mode or "none",
                "post_tts_hold_ms": int(barge_post_tts_hold_ms),
                "allow_ptt": bool(barge_allow_ptt),
                "require_asr_evidence": bool(barge_require_asr_evidence),
            }
        },
        "asr": {"vad": {"enabled": bool(local_vad_allowed)}}
    }

    cfg["feature_manual_barge_in"] = manual_feature_enabled
    cfg["barge_in_mode_manual"] = manual_mode_manual_only
    cfg["auto_commit_when_ready"] = auto_commit_when_ready
    cfg["policy_snapshot"] = resolved_policy_snapshot

    def _sanitize_session_config_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            sanitized_dict: Dict[str, Any] = {}
            for key, item in value.items():
                try:
                    key_str = str(key)
                except Exception:
                    continue
                if key_str.startswith("_"):
                    continue
                sanitized_dict[key_str] = _sanitize_session_config_value(item)
            return sanitized_dict
        if isinstance(value, (list, tuple)):
            return [_sanitize_session_config_value(item) for item in value]
        if isinstance(value, set):
            sanitized_list = [_sanitize_session_config_value(item) for item in value]
            return sorted(sanitized_list, key=lambda item: repr(item))
        if isinstance(value, bytes):
            try:
                return base64.b64encode(value).decode("ascii")
            except Exception:
                return repr(value)
        return repr(value)

    def _session_config_snapshot() -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        for key, value in cfg.items():
            try:
                key_str = str(key)
            except Exception:
                continue
            if key_str.startswith("_"):
                continue
            snapshot[key_str] = _sanitize_session_config_value(value)
        return snapshot

    def _emit_session_config_if_ready() -> None:
        nonlocal flow_session_config_emitted
        if flow_session_config_emitted:
            return
        snapshot = _session_config_snapshot()
        config_hash = ""
        try:
            canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            canonical = ""
        if canonical:
            try:
                config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            except Exception:
                config_hash = ""
        meta: Dict[str, Any] = {"config": snapshot}
        if config_hash:
            meta["config_hash"] = config_hash
        _emit_flow_event("session_config", phase="session", meta=meta)
        flow_session_config_emitted = True

    def _ensure_session_ready_emitted() -> None:
        nonlocal flow_session_ready_emitted
        if flow_session_ready_emitted:
            return
        _emit_flow_event("session_ready", phase="session")
        flow_session_ready_emitted = True
        _emit_session_config_if_ready()

    def _maybe_emit_greet_end(*, via: str = "") -> None:
        nonlocal greet_end_pending, greet_end_emitted
        if greet_end_emitted or not greet_end_pending:
            return
        _emit_flow_event("greet_end", phase="greet")
        greet_end_emitted = True
        greet_end_pending = False

    loop = asyncio.get_running_loop()
    barge = BargeState()
    last_barge_phase = [None]
    barge_pause_meta_ref: List[Optional[Dict[str, Any]]] = [None]

    def _emit_transition_event(
        type_: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
        phase: str = "barge",
    ) -> str:
        payload = _with_ws_component(meta)
        try:
            return (
                flow_emit(
                    session_id=sid,
                    level="transition",
                    phase=phase,
                    type=type_,
                    who="system",
                    meta=payload,
                )
                or ""
            )
        except Exception:
            return ""

    def _emit_ws_error(code: Optional[object]) -> None:
        try:
            code_str = str(code) if code is not None else "unknown"
        except Exception:
            code_str = "unknown"
        event_id = _emit_transition_event(
            "ws_error",
            meta={"code": code_str},
            phase="transport",
        )
        try:
            _emit_state_snapshot(event_id, "transport")
        except NameError:
            pass

    def _current_tts_active() -> bool:
        try:
            current_turn_id = _current_assistant_turn_id(sid)
        except Exception:
            current_turn_id = None
        tts_state, _ = _lookup_tts_state(sid, current_turn_id)
        return _is_tts_active(tts_state)

    def _set_barge_pause_meta(
        src: str,
        *,
        tts_active: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        base: Dict[str, Any] = {}
        existing = barge_pause_meta_ref[0]
        if isinstance(existing, dict) and existing.get("src") == src:
            base.update(existing)
        base["src"] = src
        if extra:
            for key, value in extra.items():
                if key == "src":
                    continue
                base[key] = value
        if tts_active is None:
            tts_active = _current_tts_active()
        try:
            base["tts_active"] = bool(tts_active)
        except Exception:
            base["tts_active"] = bool(tts_active)
        barge_pause_meta_ref[0] = base

    def _send_barge_state(phase: str) -> None:
        if not phase:
            return
        prev = last_barge_phase[0]
        if phase == "paused":
            pause_meta = dict(barge_pause_meta_ref[0] or {})
            pause_meta.setdefault("src", "unknown")
            if pause_meta.get("src") == "client_vad":
                pause_meta["src"] = "unknown"
            if "tts_active" not in pause_meta:
                pause_meta["tts_active"] = _current_tts_active()
            _emit_transition_event("barge_in", meta=pause_meta)
        elif prev == "paused":
            if phase == "assistant_speaking":
                resume_meta: Dict[str, Any] = {}
                pause_meta = barge_pause_meta_ref[0] or {}
                src_val = pause_meta.get("src") if isinstance(pause_meta, dict) else None
                if src_val:
                    resume_meta["src"] = src_val
                resume_meta["tts_active"] = _current_tts_active()
                _emit_transition_event("barge_resume", meta=resume_meta or None)
            barge_pause_meta_ref[0] = None

        frame = {"type": "state", "phase": phase}
        try:
            bus.broadcast(sid, frame)
        except Exception:
            pass

        try:
            _emit_barge_admin_events(sid, phase, prev)
            last_barge_phase[0] = phase
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
    pending_final_texts: Dict[int, str] = {}
    completed_llm_turns: Set[int] = set()
    synthetic_final_turns: Set[int] = set()
    final_seen = [False]
    asr_seen_partial = [False]
    asr_partial_counter = [0]
    asr_partial_first_emitted = [False]
    evidence_gate_emitted = [False]
    last_confident_partial_conf: List[Optional[float]] = [None]
    last_partial_conf: List[Optional[float]] = [None]
    last_partial_speech_ms: List[int] = [0]
    last_user_final_text: List[str] = [""]
    last_partial_text: List[str] = [""]
    current_assistant_turn_ref: List[Optional[Any]] = [None]
    manual_commit_pending = [False]
    manual_button_down = [False]
    manual_turn_active = [False]
    ptt_down_emitted = [False]
    ptt_turn_preopened = [False]
    # Runtime flags to coordinate commit + VAD gating
    assistant_speaking = [False]
    tts_mask_active = [False]
    tts_mask_mode = ["none"]
    tts_mask_release_task: List[Optional[asyncio.Task]] = [None]
    tts_mask_release_deadline = [0.0]
    tts_last_end_ts = [0.0]
    vad_desired_state: List[bool] = [True]
    vad_last_reason: List[str] = ["idle"]
    vad_apply_task: List[Optional[asyncio.Task]] = [None]

    local_vad_open = [False]
    local_vad_meta_sent = [False]

    def _barge_state() -> Dict[str, Any]:
        return _BARGE_EVENT_STATE.setdefault(sid, {})

    def _coerce_ts_ms(ts: Optional[float] = None) -> int:
        base = ts if ts is not None else time.time()
        return int(max(0.0, base) * 1000)

    def _update_barge_state(**updates: Any) -> None:
        state = _barge_state()
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value

    def _note_manual_down(ts: Optional[float] = None) -> None:
        _update_barge_state(
            manual_gate=True,
            last_manual_ptt_ms=_coerce_ts_ms(ts),
            policy_triggered=False,
        )

    def _note_manual_up() -> None:
        _update_barge_state(manual_gate=False)

    def _note_client_vad_start(ts: Optional[float] = None) -> None:
        _update_barge_state(
            client_vad_active=True,
            client_vad_start_ms=_coerce_ts_ms(ts),
        )

    def _note_client_vad_stop() -> None:
        _update_barge_state(client_vad_active=False)

    def _current_gate_snapshot(tts_override: Optional[bool] = None) -> Dict[str, bool]:
        state = _barge_state()
        manual_gate = bool(state.get("manual_gate"))
        vad_auto = bool(state.get("client_vad_active"))
        tts_active_now = bool(
            tts_override
            if tts_override is not None
            else (assistant_speaking[0] or tts_mask_active[0])
        )
        return gate_snapshot(tts_active_now, manual_gate, vad_auto)

    def _make_barge_evidence() -> Dict[str, Any]:
        state = _barge_state()
        conf = state.get("last_confident_conf")
        if conf is None:
            conf = state.get("last_partial_conf")
        try:
            conf_val = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        return {
            "ms_since_last_ptt": ms_since(state.get("last_manual_ptt_ms")),
            "ms_since_vad_start": ms_since(state.get("client_vad_start_ms")),
            "dg_conf": conf_val,
        }

    def _resolve_turn_open_source(commit_mode: str) -> str:
        mode_norm = (commit_mode or "").strip().lower()
        if mode_norm == "programmatic":
            return "programmatic"
        state = _barge_state()
        conf = state.get("last_confident_conf")
        if conf is None:
            conf = state.get("last_partial_conf")
        try:
            conf_val = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        return resolve_barge_origin(
            manual_gate=bool(state.get("manual_gate")),
            client_vad_recent_ms=ms_since(state.get("client_vad_start_ms")),
            asr_conf_recent=conf_val,
            policy_triggered=bool(state.get("policy_triggered"))
            or mode_norm == "auto_commit",
        )

    def _classify_commit_source(mode: str, reason: Optional[str]) -> str:
        mode_norm = (mode or "").strip().lower()
        reason_norm = (reason or "").strip().lower()
        if mode_norm == "manual":
            return "manual_release"
        if reason_norm in {"timeout_commit", "timeout"}:
            return "silence_timeout"
        if mode_norm in {"auto", "auto_commit"}:
            return "server_policy"
        return "endpointing_asr"

    def _map_endpoint_reason(reason: Optional[str]) -> str:
        reason_norm = (reason or "").strip().lower()
        if reason_norm in {"timeout_commit", "timeout"}:
            return "timeout"
        if reason_norm in {"no_input", "silence"}:
            return "no_input"
        return "end_of_utterance"

    def _make_commit_evidence(reason: Optional[str]) -> Dict[str, Any]:
        state = _barge_state()
        metrics = dict(state.get("last_commit_metrics") or {})
        try:
            ms_speech = int(metrics.get("elapsed_ms")) if metrics.get("elapsed_ms") is not None else None
        except (TypeError, ValueError):
            ms_speech = None
        raw_silence = metrics.get("gap_ms") or metrics.get("silence_ms")
        try:
            ms_silence = int(raw_silence) if raw_silence is not None else None
        except (TypeError, ValueError):
            ms_silence = None
        conf = state.get("last_confident_conf")
        if conf is None:
            conf = state.get("last_partial_conf")
        try:
            conf_val = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        return {
            "endpoint": _map_endpoint_reason(reason or metrics.get("reason")),
            "ms_since_last_audio": ms_since(state.get("last_audio_ts_ms")),
            "ms_speech": ms_speech,
            "ms_silence": ms_silence,
            "dg_conf_at_eos": conf_val,
            "vendor_reason": metrics.get("reason") or reason,
        }

    def _classify_cancel_source(reason: Optional[str]) -> str:
        reason_norm = (reason or "").strip().lower()
        if "transport" in reason_norm or "relay" in reason_norm:
            return "transport_error"
        if reason_norm in {"close_stream", "page_unload"}:
            return "page_unload"
        if reason_norm in {"manual", "manual_cancel", "user_cancel"}:
            return "manual_cancel"
        return "policy_preempt"

    def _make_cancel_evidence(reason: Optional[str]) -> Dict[str, Any]:
        state = _barge_state()
        conf = state.get("last_confident_conf")
        if conf is None:
            conf = state.get("last_partial_conf")
        try:
            conf_val = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        reason_norm = (reason or "").strip().lower() or None
        return {
            "reason": reason_norm,
            "ms_since_last_audio": ms_since(state.get("last_audio_ts_ms")),
            "dg_conf": conf_val,
        }

    def _emit_endpoint_event(turn_id: Optional[int], source: str, reason: Optional[str]) -> None:
        admin_cb = globals().get("_admin_emit")
        if not callable(admin_cb):
            return
        payload: Dict[str, Any] = {
            "session_id": sid,
            "sid": sid,
        }
        payload.update(
            make_source_meta(
                source,
                gates=_current_gate_snapshot(),
                evidence=_make_commit_evidence(reason),
            )
        )
        if turn_id is not None:
            payload["turn_id"] = turn_id
        try:
            admin_cb("endpoint_detected", **payload)
        except Exception:
            pass

    _update_barge_state(
        manual_gate=False,
        client_vad_active=False,
        policy_triggered=False,
        last_manual_ptt_ms=None,
        client_vad_start_ms=None,
        last_partial_conf=None,
        last_confident_conf=None,
        last_commit_metrics=None,
        last_audio_ts_ms=None,
    )

    def _decide_barge_attempt(source: str) -> Tuple[bool, str, str, str]:
        src = "ptt" if str(source).lower() == "ptt" else "vad"
        try:
            current_turn_id = _current_assistant_turn_id(sid)
        except Exception:
            current_turn_id = None
        tts_state, tts_key = _lookup_tts_state(sid, current_turn_id)
        tts_active_now = _is_tts_active(tts_state)
        now_ts = time.time()
        hold_active = False
        post_hold_ms = 0
        try:
            post_hold_ms = int(barge_post_tts_hold_ms)
        except Exception:
            post_hold_ms = 0
        post_hold_ms = max(0, post_hold_ms)
        last_end = tts_last_end_ts[0]
        if (not tts_active_now) and last_end and post_hold_ms > 0:
            hold_active = now_ts < (last_end + (post_hold_ms / 1000.0))
        if tts_active_now:
            tts_phase = "TTS_ACTIVE"
        elif hold_active:
            tts_phase = "POST_TTS_HOLD"
        else:
            tts_phase = "IDLE"

        allowed = True
        reason = "ok"
        telemetry_reason = "normal"
        suppress_mode = (barge_suppress_mode or "none").strip() or "none"
        if src == "vad":
            if _manual_mode_active():
                allowed = False
                reason = "manual_interrupt_only"
                telemetry_reason = "manual_interrupt_only"
            elif tts_active_now:
                allowed = False
                reason = "tts_active"
            elif hold_active:
                allowed = False
                reason = "post_tts_hold"
                telemetry_reason = "post_tts_hold"
            elif not local_vad_allowed:
                allowed = False
                reason = "local_vad_disabled"
            elif suppress_mode == "all" and tts_mask_active[0]:
                allowed = False
                reason = "tts_mask"
        else:
            if not manual_feature_enabled:
                allowed = False
                reason = "ptt_disabled"

        ts_value = now_ts
        ts_ms_value = int(ts_value * 1000)
        event_payload: Dict[str, Any] = {
            "session_id": sid,
            "schema": "barge_decision.v1",
            "by": src,
            "state": tts_phase,
            "allowed": bool(allowed),
            "reason": telemetry_reason,
            "local_vad_open": bool(local_vad_open[0]),
            "local_vad_meta": bool(local_vad_meta_sent[0]),
            "asr_seen_partial": bool(asr_seen_partial[0]),
            "tts_active": bool(tts_active_now),
            "tts_turn_key": tts_key,
            "ts": ts_value,
            "ts_ms": ts_ms_value,
        }
        if current_turn_id is not None:
            event_payload["turn_id"] = current_turn_id
        event_payload["reason_detail"] = reason

        with contextlib.suppress(Exception):
            if _admin_emit:
                _admin_emit("barge_decision", **event_payload)

        return allowed, src, tts_phase, reason
    pending_confirm_request: List[Optional[Dict[str, Any]]] = [None]
    asr_ready = [False]
    turn_commit_mode_ref: List[str] = ["vad"]
    last_ready_signal_after: List[Optional[str]] = [None]
    active_turn_mode_ref: List[str] = ["vad"]
    turn_timing: Dict[str, List[float]] = {
        "start": [0.0],
        "dg_open": [0.0],
        "vad_open": [0.0],
        "asr_start": [0.0],
        "first_partial": [0.0],
        "final_received": [0.0],
        "final": [0.0],
        "llm_final": [0.0],
        "tts_start": [0.0],
        "tts_end": [0.0],
    }
    turn_finish_logged = [False]
    last_partial_ts: List[float] = [0.0]
    timing_summary_emitted = [False]

    asr_evidence_ref: List[Dict[str, Any]] = [
        {
            "turn_id": None,
            "open": False,
            "bytes_forwarded": 0,
            "partials_count": 0,
            "final_received": False,
            "final_reason": None,
            "vendor_status": "pending",
            "vendor_status_detail": None,
            "vendor_fault": None,
            "close_reason": None,
            "faults_emitted": set(),
            "emitted": False,
            "auto_skip_emitted": False,
        }
    ]

    final_guard_reset_ref: List[Optional[Callable[[str], None]]] = [None]
    final_guard_local_vad_ref: List[Optional[Callable[[str], None]]] = [None]

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
    no_audio_broadcast_enabled = _env_truth("WS_NO_AUDIO_NUDGE", False)
    asr_ready_evt: asyncio.Event = asyncio.Event()
    asr_ready_wait_s: float = float(os.getenv("ASR_READY_WAIT_S", "3.0"))
    max_buffered_chunks = max(1, int(os.getenv("ASR_MAX_BUFFERED_CHUNKS", "16")))
    turn_connect_started = [False]
    # When True we stream new audio chunks directly to Deepgram instead of staging
    # them locally. Enabled once the socket is open and any backlog has been
    # flushed so transcription isn't gated on the not-ready timeout.
    asr_direct_stream = [False]
    # Track whether the current turn has been committed (manual or automatic)
    # so that we know when to flip into pass-through mode.
    turn_stream_committed = [False]
    # Coordinate async activation of the ASR stream once a turn commits.
    asr_stream_activation_task: List[Optional[asyncio.Task]] = [None]
    asr_not_ready_timeout_task: List[Optional[asyncio.Task]] = [None]

    # NEW: per-turn mic capture state
    mic_chunks: List[bytes] = []
    mic_first_ts = [0.0]
    mic_last_ts = [0.0]

    def _normalize_turn_id(value: Optional[Any]) -> Optional[str]:
        if value in (None, "", "greet"):
            return None
        try:
            return str(int(value))
        except Exception:
            try:
                return str(value)
            except Exception:
                return None

    def _reset_asr_evidence(turn_id: Optional[Any]) -> None:
        turn_value = _normalize_turn_id(turn_id)
        asr_evidence_ref[0] = {
            "turn_id": turn_value,
            "open": bool(turn_value),
            "bytes_forwarded": 0,
            "partials_count": 0,
            "final_received": False,
            "final_reason": None,
            "vendor_status": "pending",
            "vendor_status_detail": None,
            "vendor_fault": None,
            "close_reason": None,
            "faults_emitted": set(),
            "emitted": False,
            "auto_skip_emitted": False,
        }

    def _note_asr_bytes(count: int) -> None:
        if count <= 0:
            return
        state = asr_evidence_ref[0]
        if not state.get("open"):
            return
        prev = int(state.get("bytes_forwarded", 0) or 0)
        new_total = prev + int(count)
        state["bytes_forwarded"] = new_total
        if new_total > 0 and prev <= 0 and turn_timing is not None:
            with contextlib.suppress(Exception):
                holder = turn_timing.setdefault("asr_start", [0.0])
                if not holder[0]:
                    holder[0] = time.time()

    def _emit_asr_evidence(reason: Optional[str]) -> None:
        state = asr_evidence_ref[0]
        if state.get("emitted"):
            return
        turn_id_str = state.get("turn_id")
        if not turn_id_str:
            return
        bytes_forwarded = int(state.get("bytes_forwarded", 0) or 0)
        partials = int(state.get("partials_count", 0) or 0)
        final_received = bool(state.get("final_received"))
        vendor_status = state.get("vendor_status") or ("ok" if final_received else "pending")
        vendor_detail = state.get("vendor_status_detail")
        close_reason = reason or state.get("close_reason") or (
            "normal" if final_received else "no_final"
        )
        final_reason = state.get("final_reason")
        meta: Dict[str, Any] = {
            "turn_id": turn_id_str,
            "bytes_forwarded": bytes_forwarded,
            "partials_count": partials,
            "final_received": final_received,
            "vendor_status": vendor_status,
            "close_reason": close_reason,
            "src": "server_asr",
            "component": "server_asr",
            "missing_source": False,
        }
        if vendor_detail:
            meta["vendor_status_detail"] = vendor_detail
        if final_reason:
            meta["final_reason"] = final_reason
        _emit_flow_event("asr_evidence", phase="turn", meta=meta)
        faults_to_emit: Set[str] = set()
        if bytes_forwarded > 0 and partials == 0:
            faults_to_emit.add("no_partials")
        if state.get("vendor_fault"):
            faults_to_emit.add("vendor_close")
        emitted_faults: Set[str] = set(state.get("faults_emitted") or set())
        for fault in faults_to_emit:
            if fault in emitted_faults:
                continue
            fault_meta = {
                "turn_id": turn_id_str,
                "fault": fault,
                "src": "server_asr",
                "component": "server_asr",
                "missing_source": False,
            }
            _emit_flow_event("asr_path_fault", phase="turn", meta=fault_meta)
            emitted_faults.add(fault)
        state["faults_emitted"] = emitted_faults
        state["emitted"] = True

    def _note_asr_evidence_event(
        kind: str, payload: Optional[Dict[str, Any]] = None
    ) -> None:
        state = asr_evidence_ref[0]
        info: Dict[str, Any] = {}
        if isinstance(payload, dict):
            info = dict(payload)
        if kind == "open":
            state["open"] = True
            state["vendor_status"] = "open"
            if turn_timing is not None:
                with contextlib.suppress(Exception):
                    holder = turn_timing.setdefault("dg_open", [0.0])
                    if not holder[0]:
                        holder[0] = time.time()
            return
        if kind == "partial":
            state["partials_count"] = int(state.get("partials_count", 0) or 0) + 1
            return
        if kind == "final":
            state["final_received"] = True
            state["final_reason"] = info.get("final_reason") or info.get(
                "final_reason_detail"
            )
            if turn_timing is not None:
                with contextlib.suppress(Exception):
                    holder = turn_timing.setdefault("final_received", [0.0])
                    if not holder[0]:
                        holder[0] = time.time()
            if state.get("vendor_status") in {"pending", "open"}:
                state["vendor_status"] = "ok"
            return
        if kind == "error":
            err_txt = info.get("error") or info.get("code") or "error"
            state["vendor_status"] = "error"
            state["vendor_status_detail"] = err_txt
            state["vendor_fault"] = "vendor_close"
            state["close_reason"] = "vendor_error"
            return
        if kind == "timeout":
            state["vendor_status"] = "timeout"
            state["vendor_status_detail"] = info.get("reason") or info.get("error")
            state["vendor_fault"] = "vendor_close"
            state["close_reason"] = "vendor_timeout"
            return
        if kind == "vendor_close":
            state["vendor_fault"] = "vendor_close"
            state["vendor_status_detail"] = info.get("reason") or info.get("error")
            if state.get("vendor_status") in {"pending", "open"}:
                state["vendor_status"] = info.get("status") or "error"
            return
        if kind == "close":
            reason = info.get("reason") or state.get("close_reason")
            state["open"] = False
            _emit_asr_evidence(reason)
            return

    def _asr_evidence_ready_for_auto() -> bool:
        state = asr_evidence_ref[0]
        turn_id_norm = _normalize_turn_id(turn_id_ref[0])
        if not turn_id_norm or state.get("turn_id") != turn_id_norm:
            return False
        if not state.get("final_received"):
            return False
        return bool(int(state.get("partials_count", 0) or 0) > 0)

    def _emit_policy_decision_skip(reason: str) -> None:
        state = asr_evidence_ref[0]
        if state.get("auto_skip_emitted"):
            return
        turn_id_norm = _normalize_turn_id(turn_id_ref[0])
        if not turn_id_norm:
            return
        decision_meta = {
            "turn_id": turn_id_norm,
            "decision": "skip_auto_commit",
            "reason": reason,
            "src": "policy",
            "component": "policy",
            "missing_source": False,
        }
        _emit_flow_event("policy_decision", phase="policy", meta=decision_meta)
        fault_meta = {
            "turn_id": turn_id_norm,
            "fault": "evidence_missing",
            "src": "policy",
            "component": "policy",
            "missing_source": False,
        }
        _emit_flow_event("policy_fault", phase="policy", meta=fault_meta)
        state["auto_skip_emitted"] = True

    def _emit_timing_summary(turn_id_value: Optional[Any]) -> None:
        if timing_summary_emitted[0]:
            return
        summary_turn = _normalize_turn_id(turn_id_value)
        if not summary_turn:
            return
        if turn_timing is None:
            return
        holder = turn_timing.get("tts_end")
        if not holder or not holder[0]:
            return
        times: Dict[str, float] = {}
        for key in (
            "vad_open",
            "asr_start",
            "first_partial",
            "final_received",
            "llm_final",
            "tts_start",
            "tts_end",
        ):
            slot = turn_timing.get(key, [0.0])
            ts = slot[0] if slot else 0.0
            if ts:
                times[key] = ts

        def _delta_ms(start_key: str, end_key: str) -> Optional[int]:
            start_ts = times.get(start_key)
            end_ts = times.get(end_key)
            if not start_ts or not end_ts:
                return None
            return int(max(0.0, (end_ts - start_ts) * 1000))

        metrics: Dict[str, int] = {}
        for label, start_key, end_key in (
            ("vad_to_asr_start_ms", "vad_open", "asr_start"),
            ("asr_start_to_first_partial_ms", "asr_start", "first_partial"),
            ("first_partial_to_final_ms", "first_partial", "final_received"),
            ("final_to_llm_final_ms", "final_received", "llm_final"),
            ("llm_final_to_tts_start_ms", "llm_final", "tts_start"),
            ("tts_start_to_tts_end_ms", "tts_start", "tts_end"),
        ):
            delta = _delta_ms(start_key, end_key)
            if delta is not None:
                metrics[label] = delta
        roundtrip = _delta_ms("vad_open", "tts_end")
        if roundtrip is not None:
            metrics["roundtrip_latency_ms"] = roundtrip
        if not metrics:
            return
        summary_meta = {
            "turn_id": summary_turn,
            "metrics": metrics,
            "src": "policy",
            "component": "policy",
            "missing_source": False,
        }
        _emit_flow_event("timing_summary", phase="policy", meta=summary_meta)
        timing_summary_emitted[0] = True

    # Local confirmation gating
    try:
        confirm_min_ms = int(cfg.get("confirm_ms", 420) or 420)
    except Exception:
        confirm_min_ms = 420
    confirm_min_ms = max(0, confirm_min_ms)
    confirm_min_ms = max(300, confirm_min_ms)
    try:
        confirm_max_ms = int(cfg.get("confirm_max_ms", confirm_min_ms + 600) or (confirm_min_ms + 600))
    except Exception:
        confirm_max_ms = confirm_min_ms + 600
    if confirm_max_ms < confirm_min_ms:
        confirm_max_ms = confirm_min_ms
    try:
        confirm_gap_ms = float(cfg.get("confirm_max_gap_ms", 180.0) or 180.0)
    except Exception:
        confirm_gap_ms = 180.0
    try:
        confirm_min_tokens = max(1, int(cfg.get("confirm_min_tokens", 2) or 2))
    except Exception:
        confirm_min_tokens = 2
    try:
        confirm_min_conf = float(cfg.get("confirm_min_confidence", 0.5) or 0.5)
    except Exception:
        confirm_min_conf = 0.5
    idle_conf_env = (os.getenv("ASR_IDLE_MIN_PARTIAL_CONF", "") or "").strip()
    if idle_conf_env:
        try:
            idle_min_partial_conf = float(idle_conf_env)
        except Exception:
            idle_min_partial_conf = 0.6
    else:
        try:
            idle_min_partial_conf = float(cfg.get("asr_idle_min_conf", 0.6) or 0.6)
        except Exception:
            idle_min_partial_conf = 0.6
    idle_min_partial_conf = max(0.0, idle_min_partial_conf)
    idle_speech_env = (os.getenv("ASR_IDLE_MIN_SPEECH_MS", "") or "").strip()
    if idle_speech_env:
        try:
            idle_min_speech_ms = int(float(idle_speech_env))
        except Exception:
            idle_min_speech_ms = 300
    else:
        try:
            idle_min_speech_ms = int(cfg.get("asr_idle_min_speech_ms", 300) or 300)
        except Exception:
            idle_min_speech_ms = 300
    idle_min_speech_ms = max(0, idle_min_speech_ms)
    idle_evidence_required = idle_min_partial_conf > 0.0 or idle_min_speech_ms > 0
    confirm_min_conf = max(0.6, confirm_min_conf, idle_min_partial_conf)
    try:
        confirm_snr_db = float(cfg.get("confirm_min_snr_db", 8.0) or 8.0)
    except Exception:
        confirm_snr_db = 8.0
    try:
        confirm_snr_slack_db = float(cfg.get("confirm_snr_slack_db", 0.5) or 0.5)
    except Exception:
        confirm_snr_slack_db = 0.5
    if confirm_snr_slack_db < 0.0:
        confirm_snr_slack_db = 0.0

    confirm_window_ref: List[Optional[ConfirmWindow]] = [None]
    confirm_timeout_task: List[Optional[asyncio.Task]] = [None]
    confirm_timeout_cancelled: List[asyncio.Task] = []
    confirm_flow_parent_id: List[Optional[str]] = [None]
    confirm_debug_state: List[Dict[str, Any]] = [
        {
            "parent_id": "",
            "opened_at": 0.0,
            "last_vad_ts": None,
            "last_audio_ts": None,
            "last_partial_ts": None,
            "vad_ticks": [],
            "audio_chunks": [],
            "dg_partials": [],
        }
    ]

    def _reset_confirm_debug(parent_id: Optional[str]) -> None:
        opened_at = time.time() if parent_id else 0.0
        confirm_debug_state[0] = {
            "parent_id": parent_id or "",
            "opened_at": opened_at,
            "last_vad_ts": None,
            "last_audio_ts": None,
            "last_partial_ts": None,
            "vad_ticks": [],
            "audio_chunks": [],
            "dg_partials": [],
        }

    def _flush_confirm_debug_batches() -> None:
        state = confirm_debug_state[0]
        parent_id = state.get("parent_id") or ""
        if parent_id:
            for kind in ("vad_ticks", "audio_chunks", "dg_partials"):
                items = state.get(kind) or []
                if items:
                    try:
                        add_batch(parent_id, kind, list(items))
                    except Exception:
                        pass
        _reset_confirm_debug(None)
        confirm_flow_parent_id[0] = None

    def _record_confirm_chunk(now_ts: float, chunk: bytes) -> None:
        state = confirm_debug_state[0]
        parent_id = state.get("parent_id") or ""
        if not parent_id:
            return
        opened_at = float(state.get("opened_at") or 0.0)
        last_audio = state.get("last_audio_ts")
        base = last_audio if last_audio else opened_at
        if base:
            dt_ms = int(max(0.0, (now_ts - base) * 1000))
        else:
            dt_ms = 0
        state["last_audio_ts"] = now_ts
        mime_value = transport.get("codec") or transport.get("container") or "unknown"
        state["audio_chunks"].append({
            "dt_ms": dt_ms,
            "bytes": len(chunk or b""),
            "mime": mime_value,
        })
        rms_val = None
        if chunk and audioop is not None:
            try:
                rms_val = audioop.rms(chunk, 2)
            except Exception:
                rms_val = None
        last_vad = state.get("last_vad_ts")
        vad_base = last_vad if last_vad else opened_at
        if vad_base:
            vad_dt = int(max(0.0, (now_ts - vad_base) * 1000))
        else:
            vad_dt = 0
        state["last_vad_ts"] = now_ts
        state["vad_ticks"].append(
            {"dt_ms": vad_dt, "rms": rms_val, "gate": bool(local_vad_open[0])}
        )

    def _record_confirm_partial(
        now_ts: float, chars: int, conf: Optional[float]
    ) -> None:
        state = confirm_debug_state[0]
        parent_id = state.get("parent_id") or ""
        if not parent_id:
            return
        opened_at = float(state.get("opened_at") or 0.0)
        last_partial = state.get("last_partial_ts")
        base = last_partial if last_partial else opened_at
        if base:
            dt_ms = int(max(0.0, (now_ts - base) * 1000))
        else:
            dt_ms = 0
        state["last_partial_ts"] = now_ts
        state["dg_partials"].append(
            {
                "dt_ms": dt_ms,
                "chars": int(chars),
                "conf": conf,
                "final": False,
            }
        )

    def _build_state_snapshot_meta(
        phase_label: str, *, queue_override: Optional[int] = None
    ) -> Dict[str, Any]:
        now_ts = time.time()
        last_age = None
        if last_partial_ts[0]:
            last_age = int(max(0.0, (now_ts - last_partial_ts[0]) * 1000))
        queue_len = queue_override if queue_override is not None else len(buffered_chunks)
        return {
            "phase": phase_label,
            "tts_active": bool(assistant_speaking[0] or tts_mask_active[0]),
            "confirm_open": bool(confirm_window_ref[0]),
            "asr_ready": bool(asr_ready[0]),
            "last_partial_age_ms": last_age,
            "queue": queue_len,
        }

    def _emit_state_snapshot(
        parent_id: Optional[str],
        phase_label: str,
        *,
        queue_override: Optional[int] = None,
    ) -> None:
        if not parent_id:
            return
        meta = _build_state_snapshot_meta(phase_label, queue_override=queue_override)
        _emit_debug_event(
            "state_snapshot",
            phase=phase_label,
            parent_id=parent_id,
            meta=meta,
        )

    def _emit_gate_checks(
        window: ConfirmWindow,
        metrics: Dict[str, Any],
        parent_id: str,
        reason_lower: str,
    ) -> None:
        if not parent_id:
            return
        checks: List[Dict[str, Any]] = []
        partial_conf = metrics.get("partial_confidence")
        if partial_conf is not None and partial_conf < window.min_confidence:
            checks.append(
                {
                    "rule": "min_confidence",
                    "value": float(partial_conf),
                    "threshold": float(window.min_confidence),
                    "passed": False,
                }
            )
        partial_tokens = metrics.get("partial_tokens")
        if partial_tokens is not None and partial_tokens < window.min_tokens:
            checks.append(
                {
                    "rule": "min_tokens",
                    "value": int(partial_tokens),
                    "threshold": int(window.min_tokens),
                    "passed": False,
                }
            )
        gap_ms = metrics.get("gap_ms")
        if gap_ms is not None and reason_lower == "gap":
            checks.append(
                {
                    "rule": "max_gap_ms",
                    "value": float(gap_ms),
                    "threshold": float(window.max_gap_ms),
                    "passed": False,
                }
            )
        snr_db = metrics.get("snr_db")
        snr_floor = metrics.get("snr_floor_db")
        if (
            window.snr_enabled
            and snr_db is not None
            and snr_floor is not None
            and snr_db < snr_floor
        ):
            checks.append(
                {
                    "rule": "snr_db",
                    "value": float(snr_db),
                    "threshold": float(snr_floor),
                    "passed": False,
                }
            )
        for payload in checks:
            _emit_debug_event(
                "gate_check",
                phase="turn",
                parent_id=parent_id,
                meta=payload,
            )
    def _maybe_emit_evidence_gate(confidence: Optional[object] = None) -> None:
        if evidence_gate_emitted[0]:
            return
        candidate = confidence
        if candidate is None:
            candidate = last_confident_partial_conf[0]
        if candidate is None:
            return
        try:
            conf_val = float(candidate)
        except Exception:
            return
        if conf_val < confirm_min_conf:
            return
        if not (local_vad_meta_sent[0] or local_vad_open[0]):
            return
        evidence_gate_emitted[0] = True
        _emit_transition_event(
            "evidence_gate_met",
            meta={
                "client_vad": True,
                "asr_partial": True,
                "conf": conf_val,
            },
            phase="asr",
        )

    def _idle_evidence_ready() -> bool:
        if not idle_evidence_required:
            return True
        if turn_commit_mode_ref[0] != "vad":
            return True
        if idle_min_partial_conf > 0.0:
            conf_val = last_partial_conf[0]
            if conf_val is None or conf_val < idle_min_partial_conf:
                return False
        if idle_min_speech_ms > 0:
            speech_val = last_partial_speech_ms[0] if last_partial_speech_ms[0] else 0
            if speech_val < idle_min_speech_ms:
                return False
        return True

    def _flow_turn_id_hint() -> int:
        try:
            current = int(turn_id_ref[0] or 0)
        except Exception:
            current = 0
        if current > 0:
            return current
        try:
            seq = int(getattr(buf, "turn_seq", 0))
        except Exception:
            seq = 0
        return seq + 1 if seq >= 0 else 0

    def _cancel_confirm_timeout() -> None:
        task = confirm_timeout_task[0]
        if task:
            if not task.done():
                task.cancel()
                confirm_timeout_cancelled.append(task)
                if confirm_flow_parent_id[0]:
                    _emit_debug_event(
                        "timer_cancel",
                        phase="turn",
                        parent_id=confirm_flow_parent_id[0],
                        meta={"name": "confirm_timeout"},
                    )
            else:
                confirm_timeout_cancelled.append(task)
        confirm_timeout_task[0] = None

    def _emit_local_vad_signal(now_ts: float) -> None:
        if local_vad_meta_sent[0]:
            return
        allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt("vad")
        if not allow_barge:
            with contextlib.suppress(Exception):
                if allow_reason and allow_reason != "ok":
                    _jlog(
                        "local_vad_ignored",
                        sid=sid,
                        by=allow_src,
                        state=allow_state,
                        reason=allow_reason,
                    )
            return
        local_vad_meta_sent[0] = True
        _maybe_emit_evidence_gate()
        _on_local_vad_start()
        if callable(final_guard_local_vad_ref[0]):
            with contextlib.suppress(Exception):
                final_guard_local_vad_ref[0]("start")
        payload = {
            "type": "meta",
            "turn_id": turn_id_ref[0],
            "meta": {"local_vad": "start", "ts": int(now_ts * 1000)},
        }
        with contextlib.suppress(Exception):
            _jlog(
                "local_vad_start",
                sid=sid,
                turn_id=turn_id_ref[0],
                ts_ms=int(now_ts * 1000),
            )
        with contextlib.suppress(Exception):
            bus.broadcast(sid, payload)
        try:
            if loop.is_closed():
                return
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

    def _schedule_confirm_timeout(window: ConfirmWindow) -> None:
        _cancel_confirm_timeout()

        wait_s = max(0.0, window.max_duration_ms / 1000.0)

        async def _timeout() -> None:
            try:
                await asyncio.sleep(wait_s)
                if confirm_window_ref[0] is not window:
                    return
                if confirm_flow_parent_id[0]:
                    _emit_debug_event(
                        "timer_fire",
                        phase="turn",
                        parent_id=confirm_flow_parent_id[0],
                        meta={"name": "confirm_timeout"},
                    )
                decision = window.timeout(time.time())
                if decision.action == "commit":
                    metrics = decision.metrics or {}
                    reason = metrics.get("reason") or "timeout_commit"
                    _finalize_confirm_commit(reason, metrics, window)
                elif decision.action == "abort":
                    metrics = decision.metrics or {}
                    reason = metrics.get("reason") or "timeout"
                    _finalize_confirm_abort(reason, metrics, window)
            except asyncio.CancelledError:
                return
            finally:
                confirm_timeout_task[0] = None

        confirm_timeout_task[0] = asyncio.create_task(_timeout())
        if confirm_flow_parent_id[0] and wait_s > 0:
            _emit_debug_event(
                "timer_start",
                phase="turn",
                parent_id=confirm_flow_parent_id[0],
                meta={"name": "confirm_timeout", "ms": int(window.max_duration_ms)},
            )

    def _finalize_confirm_commit(
        trigger: str, metrics: Dict[str, Any], window: ConfirmWindow
    ) -> None:
        if confirm_window_ref[0] is not window:
            return
        if manual_button_down[0] or manual_turn_active[0]:
            _cancel_confirm_timeout()
            confirm_window_ref[0] = None
            _reset_confirm_debug(None)
            confirm_flow_parent_id[0] = None
            _on_local_vad_stop()
            return
        _cancel_confirm_timeout()
        confirm_window_ref[0] = None
        data = {k: v for k, v in (metrics or {}).items() if v is not None}
        data.setdefault("reason", trigger)
        data.setdefault("snr_enabled", window.snr_enabled)
        _update_barge_state(last_commit_metrics=dict(data))
        _jlog("confirm_commit", sid=sid, **data)
        if post_greet_phase_active[0]:
            _emit_flow_event(
                "confirm_close",
                phase="turn",
                meta={"reason": "commit"},
            )
        _flush_confirm_debug_batches()
        _on_local_vad_stop()
        if barge.is_paused():
            try:
                barge.commit(_send_barge_state)
            except Exception:
                pass

    def _finalize_confirm_abort(
        trigger: str, metrics: Dict[str, Any], window: ConfirmWindow
    ) -> None:
        if confirm_window_ref[0] is not window:
            return
        if manual_button_down[0] or manual_turn_active[0]:
            _cancel_confirm_timeout()
            confirm_window_ref[0] = None
            _reset_confirm_debug(None)
            confirm_flow_parent_id[0] = None
            _on_local_vad_stop()
            return
        _cancel_confirm_timeout()
        confirm_window_ref[0] = None
        data = {k: v for k, v in (metrics or {}).items() if v is not None}
        data.setdefault("reason", trigger)
        data.setdefault("snr_enabled", window.snr_enabled)
        reason_value = str(data.get("reason") or "")
        reason_lower = reason_value.lower()
        is_timeout = reason_lower == "timeout"
        system_abort_reasons = {
            "tts_start",
            "manual_start",
            "close_stream",
            "cleanup",
            "shutdown",
        }
        if post_greet_phase_active[0]:
            turn_id_hint = _flow_turn_id_hint()
            if (
                not is_timeout
                and reason_lower not in system_abort_reasons
                and turn_id_hint not in flow_turn_abort_emitted
            ):
                flow_turn_abort_emitted.add(turn_id_hint)
                flow_turn_commit_emitted.discard(turn_id_hint)
                abort_reason = reason_value or "abort"
                _emit_flow_event(
                    "turn_abort",
                    phase="turn",
                    meta={"reason": abort_reason},
                )
            close_reason = "timeout" if is_timeout else "abort"
            confirm_close_event_id = _emit_flow_event(
                "confirm_close",
                phase="turn",
                meta={"reason": close_reason},
            )
        else:
            confirm_close_event_id = ""
        if confirm_close_event_id:
            _emit_gate_checks(window, data, confirm_close_event_id, reason_lower)
        _flush_confirm_debug_batches()
        cancel_payload: Dict[str, Any] = {
            "sid": sid,
            "turn_id": turn_id_ref[0],
            **data,
        }
        cancel_payload.update(
            make_source_meta(
                _classify_cancel_source(data.get("reason")),
                gates=_current_gate_snapshot(),
                evidence=_make_cancel_evidence(data.get("reason")),
            )
        )
        _jlog("confirm_abort", **cancel_payload)
        _on_local_vad_stop()
        if barge.is_paused():
            if manual_button_down[0]:
                return
            try:
                barge.cancel(_send_barge_state)
            except Exception:
                pass

        with contextlib.suppress(Exception):
            _jlog(
                "EVT_CONFIRM_ABORT",
                sid=sid,
                turn=turn_id_ref[0],
                reason=data.get("reason"),
                elapsed_ms=data.get("elapsed_ms"),
                snr_db=data.get("snr_db"),
                partials=data.get("partial_tokens"),
                conf=data.get("partial_confidence"),
            )

    def _open_confirm_window(
        now_ts: float,
        *,
        min_ms: int,
        max_ms_local: int,
        snr_threshold: float,
        policy_until_asr_ready: bool,
        is_first_turn: bool,
    ) -> None:
        window = ConfirmWindow(
            min_duration_ms=min_ms,
            max_duration_ms=max_ms_local,
            max_gap_ms=confirm_gap_ms,
            min_tokens=confirm_min_tokens,
            min_confidence=confirm_min_conf,
            snr_threshold_db=snr_threshold,
            snr_slack_db=confirm_snr_slack_db,
            snr_enabled=True,
        )
        _push_interaction_policy(_interaction_policy_tts_snapshot())
        try:
            setattr(window, "policy_until_asr_ready", policy_until_asr_ready)
        except Exception:
            pass
        window.start(now_ts)
        confirm_window_ref[0] = window
        pending_confirm_request[0] = None
        local_vad_meta_sent[0] = False
        _emit_local_vad_signal(now_ts)
        _jlog(
            "EVT_CONFIRM_OPEN",
            sid=sid,
            turn=turn_id_ref[0],
            is_first=bool(is_first_turn),
            min_ms=min_ms,
            max_ms=max_ms_local,
            until_asr_ready=policy_until_asr_ready,
        )
        _jlog(
            "confirm_start",
            sid=sid,
            turn_id=turn_id_ref[0],
            min_ms=min_ms,
            max_ms=max_ms_local,
            max_gap_ms=confirm_gap_ms,
            min_tokens=confirm_min_tokens,
            min_confidence=confirm_min_conf,
            snr_threshold_db=snr_threshold,
            snr_slack_db=confirm_snr_slack_db,
            policy_until_asr_ready=policy_until_asr_ready,
        )
        if not post_greet_phase_active[0]:
            post_greet_phase_active[0] = True
        if post_greet_phase_active[0]:
            turn_id_hint = _flow_turn_id_hint()
            meta: Dict[str, Any] = {"phase": "post_greet", "turn_id": turn_id_hint}
            confirm_event_id = _emit_flow_event("confirm_open", phase="turn", meta=meta)
        else:
            confirm_event_id = ""
        confirm_flow_parent_id[0] = confirm_event_id or None
        if confirm_event_id:
            gate_meta = {
                "min_ms": int(min_ms),
                "max_ms": int(max_ms_local),
                "max_gap_ms": float(confirm_gap_ms),
                "min_tokens": int(confirm_min_tokens),
                "min_conf": float(confirm_min_conf),
                "snr_threshold": float(snr_threshold),
                "snr_slack": float(confirm_snr_slack_db),
                "policy_until_asr_ready": bool(policy_until_asr_ready),
                "is_first_turn": bool(is_first_turn),
            }
            _emit_debug_event(
                "gate_params",
                phase="turn",
                parent_id=confirm_event_id,
                meta=gate_meta,
            )
            _reset_confirm_debug(confirm_event_id)
        else:
            _reset_confirm_debug(None)
        _schedule_confirm_timeout(window)

    def _queue_confirm_request(request: Dict[str, Any]) -> None:
        pending_confirm_request[0] = dict(request)

    def _maybe_start_pending_confirm(now_ts: Optional[float] = None) -> None:
        request = pending_confirm_request[0]
        if not request:
            return
        if request.get("policy_until_asr_ready") and not asr_ready[0]:
            return
        if tts_mask_active[0]:
            return
        if idle_evidence_required and turn_commit_mode_ref[0] == "vad" and not _idle_evidence_ready():
            return
        pending_confirm_request[0] = None
        effective_now = now_ts if now_ts is not None else time.time()
        _open_confirm_window(
            effective_now,
            min_ms=request.get("min_ms", confirm_min_ms),
            max_ms_local=request.get("max_ms_local", confirm_max_ms),
            snr_threshold=request.get("snr_threshold", confirm_snr_db),
            policy_until_asr_ready=bool(request.get("policy_until_asr_ready")),
            is_first_turn=bool(request.get("is_first_turn")),
        )

    def _cancel_tts_mask_release() -> None:
        task = tts_mask_release_task[0]
        if task and not task.done():
            task.cancel()
        tts_mask_release_task[0] = None
        tts_mask_release_deadline[0] = 0.0

    def _mark_tts_mask_off(turn_id: Any, hold_ms: int) -> None:
        previously_active = tts_mask_active[0]
        tts_mask_active[0] = False
        tts_mask_mode[0] = barge_suppress_mode or "none"
        tts_mask_release_deadline[0] = 0.0
        mask_source = "assistant_audio_end"
        if hold_ms > 0:
            mask_source = "policy_hold"
        elif manual_button_down[0]:
            mask_source = "manual_override"
        with contextlib.suppress(Exception):
            mask_meta = make_tts_mask_meta(
                mask_source,
                gates=_current_gate_snapshot(),
                evidence={
                    "utterance_id": str(turn_id)
                    if turn_id not in (None, "", "greet")
                    else None,
                    "post_tts_hold_ms": max(0, hold_ms),
                },
            )
            if turn_id in (None, "", "greet"):
                mask_meta.setdefault("phase", "greet")
            _jlog(
                "EVT_TTS_MASK_OFF",
                sid=sid,
                turn_id=turn_id,
                post_hold_ms=max(0, hold_ms),
                **mask_meta,
            )
        if previously_active:
            _maybe_start_pending_confirm()
        _schedule_vad_state(True, "idle")
        _push_interaction_policy(_interaction_policy_idle_snapshot())

    async def _await_tts_mask_release(delay_ms: int, turn_id: Any) -> None:
        try:
            await asyncio.sleep(max(0.0, delay_ms / 1000.0))
            _mark_tts_mask_off(turn_id, delay_ms)
        except asyncio.CancelledError:
            return
        finally:
            if tts_mask_release_task[0] is asyncio.current_task():
                tts_mask_release_task[0] = None

    def _start_confirm_window(now_ts: float) -> None:
        if not local_vad_allowed:
            _jlog("confirm_skip_local_vad_disabled", sid=sid)
            return
        if _manual_mode_active():
            try:
                active_turn = bus.current_assistant_turn(sid)
            except Exception:
                active_turn = None
            tts_state, _ = _lookup_tts_state(sid, active_turn)
            if _is_tts_active(tts_state):
                _jlog(
                    "confirm_skip_manual_mode_tts",
                    sid=sid,
                    phase=tts_state,
                )
            return
        if manual_button_down[0] or manual_turn_active[0]:
            return
        min_ms = confirm_min_ms
        max_ms_local = confirm_max_ms
        snr_threshold = confirm_snr_db
        policy_until_asr_ready = False
        try:
            first_turn_active = not completed_llm_turns
        except Exception:
            first_turn_active = False
        policy_source = (
            confirm_first_policy if first_turn_active else confirm_warm_policy
        )
        if isinstance(policy_source, dict):
            raw_min = policy_source.get("min_ms")
            if isinstance(raw_min, (int, float)):
                min_ms = max(0, int(raw_min))
            raw_max = policy_source.get("max_ms")
            if isinstance(raw_max, (int, float)):
                max_ms_local = max(min_ms, int(raw_max))
            if "until_asr_ready" in policy_source:
                policy_until_asr_ready = bool(policy_source.get("until_asr_ready"))
        if isinstance(snr_policy, dict):
            key = "first_turn" if first_turn_active else "warm_turn"
            raw_snr = snr_policy.get(key)
            if isinstance(raw_snr, (int, float)):
                snr_threshold = float(raw_snr)
        request = {
            "min_ms": min_ms,
            "max_ms_local": max_ms_local,
            "snr_threshold": snr_threshold,
            "policy_until_asr_ready": policy_until_asr_ready,
            "is_first_turn": first_turn_active,
        }
        if policy_until_asr_ready and not asr_ready[0]:
            _queue_confirm_request(request)
            _jlog(
                "confirm_pending_asr_ready",
                sid=sid,
                turn=turn_id_ref[0],
                is_first=bool(first_turn_active),
            )
            return
        if tts_mask_active[0]:
            _queue_confirm_request(request)
            _jlog(
                "confirm_pending_tts",
                sid=sid,
                turn=turn_id_ref[0],
                mode=tts_mask_mode[0],
            )
            return
        if idle_evidence_required and turn_commit_mode_ref[0] == "vad" and not _idle_evidence_ready():
            _queue_confirm_request(request)
            _jlog(
                "confirm_pending_idle_evidence",
                sid=sid,
                turn=turn_id_ref[0],
                conf=last_partial_conf[0],
                speech_ms=last_partial_speech_ms[0],
                min_conf=idle_min_partial_conf,
                min_speech_ms=idle_min_speech_ms,
            )
            return
        _open_confirm_window(
            now_ts,
            min_ms=min_ms,
            max_ms_local=max_ms_local,
            snr_threshold=snr_threshold,
            policy_until_asr_ready=policy_until_asr_ready,
            is_first_turn=first_turn_active,
        )

    def _handle_confirm_chunk(chunk: bytes, now_ts: float) -> None:
        window = confirm_window_ref[0]
        if not window:
            return
        _record_confirm_chunk(now_ts, chunk)
        window.set_snr_enabled(True)
        decision = window.observe_chunk(chunk, now_ts)
        if decision.action == "commit" and decision.metrics is not None:
            _finalize_confirm_commit(
                decision.metrics.get("reason") or "chunk", decision.metrics, window
            )
        elif decision.action == "abort" and decision.metrics is not None:
            _finalize_confirm_abort(
                decision.metrics.get("reason") or "chunk", decision.metrics, window
            )

    def _handle_confirm_partial(ev: Dict[str, Any]) -> None:
        window = confirm_window_ref[0]
        if not window:
            return
        now_ts = time.time()
        raw_conf = ev.get("confidence") if isinstance(ev, dict) else None
        try:
            conf_val = float(raw_conf) if raw_conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        text_preview = ev.get("text") if isinstance(ev, dict) else ""
        try:
            char_count = len(text_preview or "")
        except Exception:
            char_count = 0
        _record_confirm_partial(now_ts, char_count, conf_val)
        decision = window.observe_partial(
            ev.get("token_count"), conf_val, now_ts
        )
        if decision.action == "commit" and decision.metrics is not None:
            _finalize_confirm_commit(
                decision.metrics.get("reason") or "partial",
                decision.metrics,
                window,
            )
        elif decision.action == "abort" and decision.metrics is not None:
            _finalize_confirm_abort(
                decision.metrics.get("reason") or "partial",
                decision.metrics,
                window,
            )

    def _handle_asr_partial_with_flow(ev: Dict[str, Any]) -> None:
        allow_barge, _, _, _ = _decide_barge_attempt("vad")
        if not allow_barge:
            return
        try:
            _handle_confirm_partial(ev)
        except Exception:
            pass

        last_partial_ts[0] = time.time()

        raw_conf = ev.get("confidence") if isinstance(ev, dict) else None
        conf_val: Optional[float]
        try:
            conf_val = float(raw_conf) if raw_conf is not None else None
        except (TypeError, ValueError):
            conf_val = None
        last_partial_conf[0] = conf_val
        _update_barge_state(last_partial_conf=conf_val)

        speech_ms_val: Optional[int] = None
        if isinstance(ev, dict):
            raw_speech = ev.get("speech_ms")
            if raw_speech is not None:
                try:
                    speech_ms_val = int(float(raw_speech))
                except (TypeError, ValueError):
                    speech_ms_val = None
        if speech_ms_val is not None and speech_ms_val >= 0:
            last_partial_speech_ms[0] = speech_ms_val
            if turn_timing is not None:
                with contextlib.suppress(Exception):
                    holder = turn_timing.setdefault("voiced_ms", [0])
                    if not holder:
                        turn_timing["voiced_ms"] = [speech_ms_val]
                    elif speech_ms_val > holder[0]:
                        holder[0] = speech_ms_val

        if turn_timing is not None:
            with contextlib.suppress(Exception):
                holder = turn_timing.setdefault("first_partial", [0.0])
                if not holder[0]:
                    holder[0] = time.time()

        if not asr_partial_first_emitted[0]:
            asr_partial_first_emitted[0] = True
            meta_conf = conf_val if conf_val is not None else 0.0
            _emit_transition_event(
                "asr_partial_first",
                meta={"conf": meta_conf},
                phase="asr",
            )

        if conf_val is not None and conf_val >= confirm_min_conf:
            last_confident_partial_conf[0] = conf_val
            _update_barge_state(last_confident_conf=conf_val)
            _maybe_emit_evidence_gate(conf_val)

        if pending_confirm_request[0] and idle_evidence_required and turn_commit_mode_ref[0] == "vad":
            _maybe_start_pending_confirm(time.time())

    def _ensure_confirm_closed(reason: str) -> None:
        window = confirm_window_ref[0]
        if not window:
            return
        decision = window.cancel(reason, time.time())
        if decision.action == "abort" and decision.metrics is not None:
            _finalize_confirm_abort(reason, decision.metrics, window)

    def _schedule_vad_state(enabled: bool, reason: str, *, force: bool = False) -> None:
        desired = bool(enabled)
        reason_norm = reason if reason in {"tts", "hold", "idle"} else (
            "idle" if desired else "tts"
        )
        if not force and vad_desired_state[0] == desired and vad_last_reason[0] == reason_norm:
            return
        vad_desired_state[0] = desired
        vad_last_reason[0] = reason_norm
        _jlog("asr_vad_state", sid=sid, enabled=desired, reason=reason_norm)

        async def _apply() -> None:
            try:
                if dg is not None:
                    await dg.set_vad_events_enabled(desired)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _jlog(
                    "asr_vad_toggle_error",
                    sid=sid,
                    enabled=desired,
                    reason=reason_norm,
                    err=exc.__class__.__name__,
                )

        task = vad_apply_task[0]
        if task and not task.done():
            task.cancel()
        if loop.is_closed():
            vad_apply_task[0] = None
            return
        new_task = asyncio.create_task(_apply())
        vad_apply_task[0] = new_task

        def _clear(done: asyncio.Task) -> None:
            if vad_apply_task[0] is done:
                vad_apply_task[0] = None

        new_task.add_done_callback(_clear)
            
    def _on_assistant_tts_start(turn_id: Any) -> None:
        now_ts = time.time()
        if turn_timing is not None:
            with contextlib.suppress(Exception):
                holder = turn_timing.setdefault("tts_start", [0.0])
                if not holder[0]:
                    holder[0] = now_ts
        assistant_speaking[0] = True
        _push_interaction_policy(_interaction_policy_tts_snapshot())
        mode = (barge_suppress_mode or "none").strip() or "none"
        mask_engaged = mode != "none"
        tts_mask_mode[0] = mode
        if mask_engaged:
            tts_mask_active[0] = True
        else:
            tts_mask_active[0] = False
        _cancel_tts_mask_release()
        with contextlib.suppress(Exception):
            mask_meta = make_tts_mask_meta(
                "assistant_audio_start",
                gates=_current_gate_snapshot(tts_override=True),
                evidence={
                    "utterance_id": str(turn_id)
                    if turn_id not in (None, "", "greet")
                    else None,
                },
            )
            if turn_id in (None, "", "greet"):
                mask_meta.setdefault("phase", "greet")
            _jlog("EVT_TTS_MASK_ON", sid=sid, turn_id=turn_id, mode=mode, **mask_meta)
        _schedule_vad_state(False, "tts")
        _ensure_confirm_closed("tts_start")
        if not manual_button_down[0]:
            with contextlib.suppress(Exception):
                barge.cancel(_send_barge_state)

    def _on_assistant_tts_end(turn_id: Any) -> None:
        assistant_speaking[0] = False
        now_ts = time.time()
        if turn_timing is not None:
            with contextlib.suppress(Exception):
                holder = turn_timing.setdefault("tts_end", [0.0])
                holder[0] = now_ts
        summary_turn = turn_id
        if summary_turn in (None, "", "greet"):
            summary_turn = turn_id_ref[0]
        _emit_timing_summary(summary_turn)
        tts_last_end_ts[0] = now_ts
        if turn_id in (None, "", "greet"):
            _maybe_emit_greet_end(via="tts_end")
        if tts_mask_active[0] and barge_post_tts_hold_ms > 0:
            delay_ms = barge_post_tts_hold_ms
            _cancel_tts_mask_release()
            tts_mask_release_deadline[0] = time.time() + (delay_ms / 1000.0)
            tts_mask_release_task[0] = asyncio.create_task(
                _await_tts_mask_release(delay_ms, turn_id)
            )
            _schedule_vad_state(False, "hold", force=True)
        elif tts_mask_active[0]:
            _cancel_tts_mask_release()
            _mark_tts_mask_off(turn_id, 0)
        else:
            _cancel_tts_mask_release()
            _mark_tts_mask_off(turn_id, 0)

    def _on_asr_connect_ok() -> None:
        already_ready = asr_ready[0]
        asr_ready[0] = True
        if not already_ready:
            with contextlib.suppress(Exception):
                delta_ms = int(max(0.0, (time.time() - ws_open_ts) * 1000))
                _jlog("EVT_ASR_READY", sid=sid, t_ms_since_ws=delta_ms)
        _maybe_start_pending_confirm()

    def _on_local_vad_start() -> None:
        local_vad_open[0] = True
        _note_client_vad_start(time.time())
        _maybe_emit_evidence_gate()
        if turn_timing is not None:
            with contextlib.suppress(Exception):
                holder = turn_timing.setdefault("vad_open", [0.0])
                if not holder[0]:
                    holder[0] = time.time()

    def _on_local_vad_stop() -> None:
        local_vad_open[0] = False
        _note_client_vad_stop()

    def _open_turn_for_ptt(now_ts: float, *, pause_tts: bool = False) -> None:
        manual_button_down[0] = True
        _note_manual_down(now_ts)
        _ensure_confirm_closed("ptt_start")
        if pause_tts:
            try:
                barge.start(
                    confirm_ms=0,
                    on_commit=_on_barge_commit,
                    send_state=_send_barge_state,
                    auto_commit=False,
                )
            except Exception:
                pass
        if manual_turn_active[0] or ptt_turn_preopened[0]:
            manual_turn_active[0] = True
            return
        try:
            current_assistant_turn_ref[0] = bus.current_assistant_turn(sid)
        except Exception:
            current_assistant_turn_ref[0] = None
        manual_turn_active[0] = True
        manual_commit_pending[0] = True
        active_turn_mode_ref[0] = "manual"
        turn_commit_mode_ref[0] = "manual"
        turn_id = buf.turn_seq + 1
        turn_id_ref[0] = turn_id
        pending_confirm_request[0] = None
        confirm_window_ref[0] = None
        local_vad_meta_sent[0] = False
        local_vad_open[0] = False
        buffered_chunks.clear()
        sent_any_audio[0] = False
        final_seen[0] = False
        asr_seen_partial[0] = False
        last_partial_conf[0] = None
        last_partial_speech_ms[0] = 0
        last_partial_text[0] = ""
        turn_connect_started[0] = False
        turn_stream_committed[0] = False
        asr_direct_stream[0] = False
        _cancel_asr_stream_activation()
        _cancel_asr_not_ready_timeout()
        mic_chunks.clear()
        mic_first_ts[0] = now_ts
        mic_last_ts[0] = now_ts
        _update_barge_state(
            last_partial_conf=None,
            last_confident_conf=None,
            last_audio_ts_ms=_coerce_ts_ms(now_ts),
        )
        _reset_turn_metrics(now_ts)
        _schedule_no_audio_watch(turn_id)
        with contextlib.suppress(Exception):
            pending_final_turns.append(turn_id)
            completed_llm_turns.discard(turn_id)
        with contextlib.suppress(Exception):
            turn_payload: Dict[str, Any] = {
                "sid": sid,
                "turn_id": turn_id,
                "first_bytes": 0,
                "commit_mode": "manual",
                "auto_commit": False,
                "ts_ms": int(time.time() * 1000),
            }
            turn_payload.update(
                make_source_meta(
                    _resolve_turn_open_source("manual"),
                    gates=_current_gate_snapshot(),
                    evidence=_make_barge_evidence(),
                )
            )
            _jlog("turn_start", **turn_payload)
        ptt_turn_preopened[0] = True

    def _handle_bus_frame(frame: Dict[str, Any]) -> None:
        ftype_raw = frame.get("type")
        ftype = (ftype_raw or "").lower()
        if ftype == "results":
            role_like = (
                frame.get("role")
                or frame.get("source")
                or frame.get("speaker")
                or ""
            )
            is_assistant = str(role_like).strip().lower() == "assistant"
            is_final = bool(frame.get("is_final"))
            if is_assistant and is_final and turn_timing is not None:
                with contextlib.suppress(Exception):
                    holder = turn_timing.setdefault("llm_final", [0.0])
                    if not holder[0]:
                        holder[0] = time.time()
        if not ftype:
            return
        turn_id = frame.get("turn_id")
        if ftype in {"tts_start", "assistant_audio", "tts:start"}:
            _on_assistant_tts_start(turn_id)
            if ftype == "assistant_audio":
                is_last_val = frame.get("is_last")
                is_last = False
                if isinstance(is_last_val, bool):
                    is_last = is_last_val
                elif isinstance(is_last_val, str):
                    is_last = is_last_val.strip().lower() in {"1", "true", "yes"}
                elif isinstance(is_last_val, (int, float)):
                    is_last = bool(is_last_val)
                if is_last:
                    _on_assistant_tts_end(turn_id)
        elif ftype in {
            "utteranceend",
            "utterance_end",
            "tts_end",
            "tts:stop",
            "tts_stop",
        }:
            _on_assistant_tts_end(turn_id)

    _ensure_bus_task_started()

    def _reset_turn_metrics(start_ts: float) -> None:
        turn_timing["start"][0] = start_ts
        for key in (
            "dg_open",
            "vad_open",
            "asr_start",
            "first_partial",
            "final_received",
            "final",
            "llm_final",
            "tts_start",
            "tts_end",
        ):
            turn_timing[key][0] = 0.0
        turn_finish_logged[0] = False
        timing_summary_emitted[0] = False
        asr_partial_counter[0] = 0
        asr_partial_first_emitted[0] = False
        evidence_gate_emitted[0] = False
        last_confident_partial_conf[0] = None
        last_partial_ts[0] = 0.0
        _reset_asr_evidence(turn_id_ref[0])

    def _emit_no_audio_alert(reason: str) -> None:
        if no_audio_notified[0]:
            return
        if sent_any_audio[0]:
            return
        if assistant_speaking[0] or tts_mask_active[0]:
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
        with contextlib.suppress(Exception):
            emit_watchdog_user_end(sid, turn_id=turn_id, source="watchdog")

    def _manual_buffered_bytes() -> int:
        total = 0
        try:
            total += sum(len(chunk) for chunk in buffered_chunks)
        except Exception:
            pass
        try:
            buf_chunks = getattr(buf, "_buf", None)
            if buf_chunks:
                total += sum(
                    len(chunk)
                    for chunk in buf_chunks
                    if isinstance(chunk, (bytes, bytearray))
                )
        except Exception:
            pass
        return total

    def _manual_mode_active() -> bool:
        return manual_feature_enabled and manual_mode_manual_only

    def _manual_log_event(name: str, **fields: Any) -> None:
        _jlog(
            name,
            sid=sid,
            admin_event=name,
            admin_label=name,
            **fields,
        )

    def _session_ready_for_auto_commit() -> bool:
        if manual_button_down[0] or manual_turn_active[0]:
            return False
        if barge.is_paused():
            return False
        if assistant_speaking[0] or tts_mask_active[0]:
            return False
        try:
            active_turn = bus.current_assistant_turn(sid)
        except Exception:
            active_turn = None
        tts_state, _ = _lookup_tts_state(sid, active_turn)
        if _is_tts_active(tts_state):
            return False
        return True

    def _has_dual_evidence() -> bool:
        if manual_button_down[0] or manual_turn_active[0]:
            return True
        local_vad_seen = bool(local_vad_meta_sent[0] or local_vad_open[0])
        if not local_vad_seen:
            return False
        return bool(asr_seen_partial[0])

    def _can_auto_commit_now() -> bool:
        if not auto_commit_when_ready:
            return False
        if not _session_ready_for_auto_commit():
            return False
        if not asr_ready[0]:
            return False
        if auto_commit_requires_asr_ready and not asr_ready[0]:
            return False
        if auto_commit_requires_dual and not _has_dual_evidence():
            return False
        if barge_require_asr_evidence and not asr_seen_partial[0]:
            return False
        local_vad_signal = bool(local_vad_meta_sent[0] or local_vad_open[0])
        if not local_vad_signal and not sent_any_audio[0]:
            return False
        return True

    def _cancel_asr_stream_activation() -> None:
        task = asr_stream_activation_task[0]
        if task and not task.done():
            task.cancel()
        asr_stream_activation_task[0] = None

    def _cancel_asr_not_ready_timeout() -> None:
        task = asr_not_ready_timeout_task[0]
        if task and not task.done():
            task.cancel()
        asr_not_ready_timeout_task[0] = None

    def _schedule_asr_not_ready_timeout() -> None:
        _cancel_asr_not_ready_timeout()
        wait_s = max(0.0, asr_ready_wait_s)
        if wait_s <= 0:
            return

        async def _timer() -> None:
            try:
                await asyncio.sleep(wait_s)
                if not asr_ready_evt.is_set():
                    _jlog("asr_not_ready_timeout", sid=sid, phase="commit")
            except asyncio.CancelledError:
                return

        asr_not_ready_timeout_task[0] = asyncio.create_task(_timer())

    async def _run_asr_stream_activation(trigger: str) -> None:
        nonlocal dg_connect_task
        try:
            try:
                queued_chunks = len(buffered_chunks)
            except Exception:
                queued_chunks = 0
            _jlog(
                "asr_stream_activate",
                sid=sid,
                trigger=trigger,
                queued=queued_chunks,
                dg_state=dg_state,
            )

            if not _has_deepgram_key():
                asr_direct_stream[0] = True
                return

            if dg_state == "closed" and dg_connect_task is None:
                if not turn_connect_started[0]:
                    turn_connect_started[0] = True
                    _jlog(
                        "asr_connect_schedule",
                        sid=sid,
                        turn_id=turn_id_ref[0],
                        queued=len(buffered_chunks),
                        dg_state=dg_state,
                    )
                dg_connect_task = asyncio.create_task(_ensure_dg_connected())

            if dg_connect_task is not None and not asr_ready_evt.is_set():
                _schedule_asr_not_ready_timeout()
                try:
                    await asyncio.wait_for(
                        asr_ready_evt.wait(), timeout=asr_ready_wait_s
                    )
                except asyncio.TimeoutError:
                    _jlog("asr_not_ready_timeout", sid=sid, phase="commit_wait")
                finally:
                    _cancel_asr_not_ready_timeout()

            if dg_connect_task is not None and not dg_connect_task.done():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        dg_connect_task, timeout=asr_ready_wait_s
                    )

            await _flush_buffered_chunks()

            if dg_state == "open" and not buffered_chunks:
                asr_direct_stream[0] = True
        finally:
            if asr_stream_activation_task[0] is asyncio.current_task():
                asr_stream_activation_task[0] = None

    def _schedule_asr_stream_activation(trigger: str) -> None:
        _cancel_asr_stream_activation()
        asr_stream_activation_task[0] = asyncio.create_task(
            _run_asr_stream_activation(trigger)
        )

    def _on_barge_commit(forced_mode: Optional[str] = None) -> None:
        mode_raw = forced_mode or turn_commit_mode_ref[0]
        pending_manual = manual_commit_pending[0]
        mode = mode_raw or ("manual" if pending_manual else "vad")
        manual_commit_pending[0] = False
        turn_commit_mode_ref[0] = "vad"
        active_turn_mode_ref[0] = mode or "vad"
        event_mode = "manual" if (mode == "manual" or pending_manual) else "auto"
        if event_mode == "auto" and not _asr_evidence_ready_for_auto():
            _emit_policy_decision_skip("no_asr_evidence")
            return
        dual_evidence = True if event_mode == "manual" else _has_dual_evidence()
        asr_ready_flag = bool(asr_ready[0])
        target_turn = current_assistant_turn_ref[0]
        try:
            latest = bus.current_assistant_turn(sid)
            if latest:
                target_turn = latest
        except Exception:
            pass
        cancel_reason: Optional[str] = None
        if target_turn:
            with contextlib.suppress(Exception):
                bus.cancel_turn(sid, target_turn)
                cancel_reason = "assistant_cancel"
        last_ready_signal_after[0] = cancel_reason
        _send_barge_state("ready")
        if last_ready_signal_after[0]:
            with contextlib.suppress(Exception):
                _jlog(
                    "EVT_READY_SIGNAL",
                    sid=sid,
                    after=last_ready_signal_after[0],
                    turn=turn_id_ref[0],
                )
            last_ready_signal_after[0] = None
        commit_metrics = dict(_barge_state().get("last_commit_metrics") or {})
        commit_reason = commit_metrics.get("reason")
        commit_source = _classify_commit_source(mode or "", commit_reason)
        commit_payload: Dict[str, Any] = {
            "sid": sid,
            "mode": mode,
            "manual": mode == "manual",
            "auto_commit": mode == "auto_commit",
            "turn_id": turn_id_ref[0],
            "ts_ms": int(time.time() * 1000),
            "admin_event": "turn_committed",
            "admin_label": "turn_committed",
        }
        commit_payload.update(
            make_source_meta(
                commit_source,
                gates=_current_gate_snapshot(),
                evidence=_make_commit_evidence(commit_reason),
            )
        )
        _jlog("turn_committed", **commit_payload)
        endpoint_source = (
            "silence_timer"
            if commit_source == "silence_timeout"
            else "manual_release" if commit_source == "manual_release" else "asr_endpoint"
        )
        _emit_endpoint_event(turn_id_ref[0], endpoint_source, commit_reason)
        _update_barge_state(last_commit_metrics=None, policy_triggered=False)
        commit_event_id = ""
        if post_greet_phase_active[0]:
            turn_id_hint = _flow_turn_id_hint()
            if turn_id_hint not in flow_turn_commit_emitted:
                flow_turn_commit_emitted.add(turn_id_hint)
                flow_turn_abort_emitted.discard(turn_id_hint)
                commit_event_id = _emit_flow_event(
                    "turn_commit",
                    phase="turn",
                    meta={"turn_id": turn_id_hint},
                )
        if not commit_event_id:
            parent_event_id = confirm_flow_parent_id[0] or ""
        else:
            parent_event_id = commit_event_id
        if parent_event_id:
            tts_end_ts = turn_timing.get("tts_end", [0.0])[0]
            first_partial_ts = turn_timing.get("first_partial", [0.0])[0]
            if tts_end_ts and first_partial_ts and first_partial_ts >= tts_end_ts:
                latency_ms = int(max(0.0, (first_partial_ts - tts_end_ts) * 1000))
                _emit_debug_event(
                    "latency_tick",
                    phase="turn",
                    parent_id=parent_event_id,
                    meta={
                        "from": "tts_end",
                        "to": "asr_partial_first",
                        "ms": latency_ms,
                    },
                )
        with contextlib.suppress(Exception):
            _jlog(
                "EVT_COMMIT",
                sid=sid,
                turn=turn_id_ref[0],
                mode=event_mode,
                dual_evidence=bool(dual_evidence),
                asr_ready=asr_ready_flag,
            )
        if not turn_stream_committed[0]:
            turn_stream_committed[0] = True
            asr_direct_stream[0] = True
            _schedule_asr_stream_activation(mode or "vad")

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
        latency_ms: Dict[str, Optional[int]] = {
            "asr_final": delta_start,
            "final_from_dg_open": delta_open,
            "final_from_first_partial": delta_partial,
        }
        if start_ts and dg_open_ts:
            latency_ms["dg_connect"] = int(max(0.0, (dg_open_ts - start_ts) * 1000))
        if start_ts and first_partial_ts:
            latency_ms["first_partial_from_mic_start"] = int(
                max(0.0, (first_partial_ts - start_ts) * 1000)
            )
        if dg_open_ts and first_partial_ts:
            latency_ms["first_partial_from_dg_open"] = int(
                max(0.0, (first_partial_ts - dg_open_ts) * 1000)
            )
        latency_ms_clean = {k: v for k, v in latency_ms.items() if v is not None}
        diag_metrics = {
            "mic_to_final_ms": latency_ms_clean.get("asr_final"),
            "final_from_dg_open_ms": latency_ms_clean.get("final_from_dg_open"),
            "final_from_first_partial_ms": latency_ms_clean.get("final_from_first_partial"),
            "mic_to_first_partial_ms": latency_ms_clean.get("first_partial_from_mic_start"),
            "dg_connect_ms": latency_ms_clean.get("dg_connect"),
        }
        with contextlib.suppress(Exception):
            note_turn_commit_latency(sid, turn_id, diag_metrics)
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
            _jlog(
                "latency_breakdown",
                sid=sid,
                turn_id=turn_id,
                ms=latency_ms_clean,
            )
        admin_cb = _admin_emit if callable(_admin_emit) else None
        latency_payload = {
            "session_id": sid,
            "turn_id": turn_id,
            "ms": latency_ms_clean,
            "reason": reason,
            "synthetic": bool(synthetic),
        }
        if admin_cb:
            with contextlib.suppress(Exception):
                admin_cb("latency_breakdown", **latency_payload)
        frame = {"type": "latency_breakdown", **latency_payload}
        with contextlib.suppress(Exception):
            asyncio.create_task(_ws_send_json(send, frame))

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
        text = (
            transcript
            or pending_final_texts.pop(turn_id, "")
            or last_user_final_text[0]
            or last_partial_text[0]
        )

        payload = make_results(turn_id, transcript=text, is_final=True)
        payload["type"] = "Results"
        await _ws_send_json(send, payload)
        utterance_payload = make_utterance_end(turn_id)
        utterance_payload["type"] = "UtteranceEnd"
        await _ws_send_json(send, utterance_payload)

        with contextlib.suppress(Exception):
            _jlog("ws_synthetic_final", sid=sid, turn_id=turn_id, reason=reason)

        if _admin_emit:
            final_payload: Dict[str, Any] = {
                "session_id": sid,
                "turn_id": turn_id,
            }
            if text:
                final_payload["text"] = text
                final_payload["text_preview"] = _clip_text(text)
            with contextlib.suppress(Exception):
                _admin_emit("asr:final", **final_payload)

        llm_scheduled = False

        if text:
            dialog_nlu_pre: Dict[str, Any] = {}
            universal_pre: Dict[str, Any] = {}
            meta_stub = {"source": "user_ws", "channel": "ws"}
            try:
                prepared_meta, dialog_nlu_raw, _ = prepare_turn_metadata(text, dict(meta_stub))
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

            if turn_id not in completed_llm_turns:
                completed_llm_turns.add(turn_id)

                async def _bg_turn(meta_payload=meta_overrides, final_text=text):
                    try:
                        await asyncio.to_thread(
                            run_ws_user_turn,
                            sid,
                            final_text,
                            None,
                            meta_overrides=meta_payload,
                        )
                    except Exception as e:
                        with contextlib.suppress(Exception):
                            await _ws_send_json(
                                send,
                                make_error("llm_turn_fail", e.__class__.__name__),
                            )

                asyncio.create_task(_bg_turn())
                llm_scheduled = True

        _log_turn_finish(
            turn_id,
            reason=reason,
            synthetic=True,
            transcript_chars=len(text or ""),
        )
        completed_llm_turns.discard(turn_id)

        if not llm_scheduled:
            try:
                cancel_target: Optional[str] = None
                if turn_stream_committed[0] or (ws_configured and barge.is_paused()):
                    try:
                        cancel_target = bus.current_assistant_turn(sid)
                    except Exception:
                        cancel_target = None
                    if cancel_target:
                        with contextlib.suppress(Exception):
                            bus.cancel_turn(sid, cancel_target)
                if should_emit_phase(sid, "ready"):
                    set_phase(sid, "ready", emitted=True)
                    ready_payload = {"type": "state", "phase": "ready"}
                    await _ws_send_json(send, ready_payload)
                    with contextlib.suppress(Exception):
                        bus.broadcast(sid, dict(ready_payload))
                else:
                    set_phase(sid, "ready")
            except Exception:
                pass

        return True

    async def _ensure_dg_connected() -> bool:
        nonlocal dg, rx_task, dg_connect_task, dg_state

        if not _has_deepgram_key():
            with contextlib.suppress(Exception):
                _jlog("asr_connect_skip", sid=sid, reason="no_api_key")
            return False

        if dg_state == "open" and dg is not None:
            asr_ready[0] = True            
            return True

        if dg_state == "connecting" and dg_connect_task is not None:
            with contextlib.suppress(Exception):
                _jlog("asr_connect_wait", sid=sid, reason="existing_task")
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
                asr_ready[0] = False                    
                asr_direct_stream[0] = False

                cfg["_transport"] = transport
                cfg["_jlog"] = _jlog
                cfg.setdefault("session_id", sid)
                cfg["_url_tag"] = f"{WS_ASGI_BUILD}:{sid}"

                def _diag_hook(label: str, **payload: Any) -> None:
                    payload_copy = dict(payload) if payload else {}
                    try:
                        if label == "asr_error":
                            _note_asr_evidence_event("error", payload_copy)
                        elif label == "asr_timeout":
                            _note_asr_evidence_event("timeout", payload_copy)
                        elif label == "connection_closed":
                            _note_asr_evidence_event("vendor_close", payload_copy)
                    except Exception:
                        pass
                    admin_cb = _admin_emit if callable(_admin_emit) else None
                    if admin_cb:
                        try:
                            admin_cb("asr:diag", label=label, **payload_copy)
                        except Exception:
                            pass

                cfg["_diag_hook"] = _diag_hook
                _jlog("asr_connect_begin", sid=sid, transport=transport)
                client = DeepgramClient(cfg)
                dg = client
                try:
                    await client.set_vad_events_enabled(vad_desired_state[0])
                except Exception:
                    pass
                await client.connect()
                ready_evt = getattr(client, "_open_evt", None)
                if isinstance(ready_evt, asyncio.Event):
                    with contextlib.suppress(Exception):
                        if not ready_evt.is_set():
                            ready_evt.set()
                else:
                    try:
                        evt = asyncio.Event()
                        evt.set()
                        setattr(client, "_open_evt", evt)
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
                        turn_timing,
                        _log_turn_finish,
                        _handle_asr_partial_with_flow,
                        on_evidence=_note_asr_evidence_event,
                        final_guard_hooks={
                            "reset_ref": final_guard_reset_ref,
                            "local_vad_ref": final_guard_local_vad_ref,
                        },
                        on_transport_error=_emit_ws_error,
                    )
                )
                _jlog("asr_connect_ok", sid=sid)
                _on_asr_connect_ok()
                _cancel_asr_not_ready_timeout()

                queued_chunks = len(buffered_chunks)
                queued_bytes = 0
                try:
                    queued_bytes = sum(
                        len(chunk)
                        for chunk in buffered_chunks
                        if isinstance(chunk, (bytes, bytearray))
                    )
                except Exception:
                    queued_bytes = 0

                with contextlib.suppress(Exception):
                    if not asr_ready_evt.is_set():
                        asr_ready_evt.set()

                _jlog(
                    "flush_on_open",
                    sid=sid,
                    queued_chunks=queued_chunks,
                    queued_bytes=queued_bytes,
                )

                await _flush_buffered_chunks()

                if dg_state == "open" and not buffered_chunks:
                    asr_direct_stream[0] = True
            except Exception as e:
                dg_state = "closed"
                dg = None
                asr_direct_stream[0] = False
                asr_ready[0] = False                
                _cancel_asr_not_ready_timeout()
                _jlog("asr_connect_fail", sid=sid, err=type(e).__name__)
                with contextlib.suppress(Exception):
                    await _ws_send_json(
                        send, make_error("asr_connect_fail", type(e).__name__)
                    )
                _emit_ws_error("asr_connect_fail")
                with contextlib.suppress(Exception):
                    if _admin_emit:
                        err_msg = f"connect:{type(e).__name__}"
                        _admin_emit(
                            "asr:error",
                            session_id=sid,
                            turn_id=turn_id_ref[0],
                            msg=err_msg,
                            error=err_msg,
                        )
            finally:
                dg_connect_task = None

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

    def _recover_transcript_from_client(client: Any) -> Optional[str]:
        if client is None:
            return None
        queue = getattr(client, "_queue", None)
        if isinstance(queue, asyncio.Queue):
            drained: List[Any] = []
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                drained.append(item)
            for item in drained:
                if isinstance(item, dict):
                    text_val = (item.get("text") or "").strip()
                    if text_val:
                        return text_val
            return None
        pending = getattr(client, "_pending_events", None)
        if isinstance(pending, (list, tuple)):
            for item in pending:
                if isinstance(item, dict):
                    text_val = (item.get("text") or "").strip()
                    if text_val:
                        return text_val
        return None

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
                parent_id = _ensure_ws_parent_id()
                if parent_id:
                    _emit_debug_event(
                        "ws_backpressure",
                        phase="session",
                        parent_id=parent_id,
                        meta={
                            "queue_len": queue_len,
                            "dropped": backpressure_drop_count,
                        },
                    )
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
            _note_asr_bytes(len(data) if isinstance(data, (bytes, bytearray)) else 0)
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
        nonlocal dg, backpressure_drop_count, bus_task
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
                if buffered_chunks:
                    buffered_chunks.popleft()
                flushed_chunks += 1
                flushed_bytes += chunk_len
                if callable(final_guard_reset_ref[0]):
                    with contextlib.suppress(Exception):
                        final_guard_reset_ref[0]("flush")
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
            if dg_state == "open":
                asr_direct_stream[0] = True

        if bus_task is None:
            bus_task = asyncio.create_task(
                _pump_bus_to_client(sid, partial(send, route="bus"), _handle_bus_frame)
            )

    _ensure_session_ready_emitted()

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
                    if ws_frames_in % _WS_FRAME_SAMPLE == 0:
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
                        parent_id = _ensure_ws_parent_id()
                        if parent_id:
                            _emit_debug_event(
                                "ws_frame_in",
                                phase="session",
                                parent_id=parent_id,
                                meta={
                                    "type": "binary",
                                    "bytes": len(chunk or b""),
                                    "route": "client",
                                },
                            )
                    raw_chunk = chunk

                    preopened_turn = ptt_turn_preopened[0]
                    new_turn = buf.is_empty() and not preopened_turn
                    if turn_id_ref[0]:
                        turn_hint = turn_id_ref[0]
                    else:
                        turn_hint = buf.turn_seq + (1 if (new_turn or preopened_turn) else 0)
                    if chunk:
                        _jlog(
                            "ws_audio_chunk",
                            sid=sid,
                            bytes=len(chunk),
                            turn_id=turn_hint,
                            ts_ms=int(now * 1000),
                            frame_seq=ws_frames_in,
                        )
                        if not audio_sig_logged:
                            with contextlib.suppress(Exception):
                                _jlog(
                                    "audio_sig",
                                    sid=sid,
                                    first8_hex=chunk[:8].hex(),
                                    turn_id=turn_hint,
                                    ts_ms=int(now * 1000),
                                )
                            audio_sig_logged = True
                    else:
                        with contextlib.suppress(Exception):
                            _jlog(
                                "ws_audio_chunk_empty",
                                sid=sid,
                                bytes=len(frame_bytes or b""),
                                turn_id=turn_hint,
                                buf_empty=new_turn,
                                ts_ms=int(now * 1000),
                                frame_seq=ws_frames_in,
                            )
                    if new_turn:
                        try:
                            current_assistant_turn_ref[0] = bus.current_assistant_turn(sid)
                        except Exception:
                            current_assistant_turn_ref[0] = None

                        commit_mode = "vad"
                        if manual_turn_active[0]:
                            commit_mode = "manual"
                        elif manual_mode_manual_only:
                            if not auto_commit_when_ready:
                                _jlog(
                                    "manual_mode_voice_chunk_ignored",
                                    sid=sid,
                                    bytes=len(raw_chunk or b""),
                                    reason="auto_commit_disabled",
                                )
                                continue
                            if not _session_ready_for_auto_commit():
                                _jlog(
                                    "manual_mode_voice_chunk_ignored",
                                    sid=sid,
                                    bytes=len(raw_chunk or b""),
                                    reason="session_not_ready",
                                )
                                continue
                            commit_mode = "auto_commit"

                        active_turn_mode_ref[0] = commit_mode
                        turn_commit_mode_ref[0] = commit_mode
                        _update_barge_state(
                            policy_triggered=commit_mode == "auto_commit"
                        )
                        if commit_mode != "manual":
                            manual_commit_pending[0] = False

                        confirm_ms = 420
                        try:
                            confirm_ms = int(cfg.get("confirm_ms", 420) or 0)
                        except Exception:
                            confirm_ms = 420
                        barge_started = False
                        should_pause = not (
                            manual_mode_manual_only and commit_mode == "auto_commit"
                        )
                        if should_pause:
                            active_assistant_turn = current_assistant_turn_ref[0]
                            tts_state, _tts_key = _lookup_tts_state(
                                sid, active_assistant_turn
                            )
                            if _is_tts_active(tts_state):
                                should_pause = False
                                try:
                                    _jlog(
                                        "barge_skip_tts_active",
                                        sid=sid,
                                        turn_hint=turn_hint,
                                    )
                                except Exception:
                                    pass
                        if not manual_turn_active[0] and should_pause:
                            allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt(
                                "vad"
                            )
                            if allow_barge:
                                try:
                                    barge_started = barge.start(
                                        confirm_ms=confirm_ms,
                                        on_commit=_on_barge_commit,
                                        send_state=_send_barge_state,
                                        auto_commit=False,
                                    )
                                except Exception:
                                    barge_started = False
                            else:
                                barge_started = False
                                with contextlib.suppress(Exception):
                                    if allow_reason and allow_reason != "ok":
                                        _jlog(
                                            "barge_attempt_ignored",
                                            sid=sid,
                                            by=allow_src,
                                            state=allow_state,
                                            reason=allow_reason,
                                        )
                        turn_id_ref[0] = buf.turn_seq + 1
                        pending_confirm_request[0] = None
                        last_partial_conf[0] = None
                        last_partial_speech_ms[0] = 0
                        last_partial_text[0] = ""
                        if barge_started:
                            if manual_mode_manual_only:
                                try:
                                    barge.commit(_send_barge_state)
                                except Exception:
                                    pass
                            else:
                                _start_confirm_window(now)
                        else:
                            confirm_window_ref[0] = None
                            local_vad_meta_sent[0] = False
                            local_vad_open[0] = False
                        with contextlib.suppress(Exception):
                            pending_final_turns.append(turn_id_ref[0])
                            completed_llm_turns.discard(turn_id_ref[0])
                        final_seen[0] = False
                        asr_seen_partial[0] = False
                        sent_any_audio[0] = False
                        buffered_chunks.clear()
                        turn_connect_started[0] = False
                        turn_stream_committed[0] = False
                        asr_direct_stream[0] = False
                        _cancel_asr_stream_activation()
                        _cancel_asr_not_ready_timeout()
                        # reset mic capture
                        mic_chunks.clear()
                        mic_first_ts[0] = now
                        mic_last_ts[0] = now
                        _update_barge_state(
                            last_partial_conf=None,
                            last_confident_conf=None,
                            last_audio_ts_ms=_coerce_ts_ms(now),
                        )
                        _reset_turn_metrics(now)
                        _schedule_no_audio_watch(turn_id_ref[0])
                        with contextlib.suppress(Exception):
                            turn_payload: Dict[str, Any] = {
                                "sid": sid,
                                "turn_id": turn_id_ref[0],
                                "first_bytes": len(raw_chunk),
                                "commit_mode": commit_mode,
                                "auto_commit": commit_mode == "auto_commit",
                                "ts_ms": int(time.time() * 1000),
                            }
                            turn_payload.update(
                                make_source_meta(
                                    _resolve_turn_open_source(commit_mode),
                                    gates=_current_gate_snapshot(),
                                    evidence=_make_barge_evidence(),
                                )
                            )
                            _jlog("turn_start", **turn_payload)
                        if commit_mode == "auto_commit":
                            try:
                                _on_barge_commit("auto")
                            except Exception:
                                pass
                    elif preopened_turn:
                        ptt_turn_preopened[0] = False
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

                    chunk = raw_chunk
                    buf.append(chunk)

                    # capture bytes for diagnostic playback
                    if MIC_CAPTURE:
                        mic_chunks.append(chunk)
                        mic_last_ts[0] = now
                    _update_barge_state(last_audio_ts_ms=_coerce_ts_ms(now))
                    _jlog(
                        "mic_capture_append",
                        sid=sid,
                        turn_id=turn_id_ref[0],
                        chunks=len(mic_chunks),
                        last_bytes=len(chunk),
                        ts_ms=int(time.time() * 1000),
                    )

                    _handle_confirm_chunk(raw_chunk, now)

                    if not _has_deepgram_key():
                        _jlog("ws_audio_no_key", sid=sid, bytes=len(chunk))
                        continue

                    # Stage early frames or stream directly when ASR is ready
                    if chunk:
                        if callable(final_guard_reset_ref[0]):
                            with contextlib.suppress(Exception):
                                final_guard_reset_ref[0]("chunk")

                        direct_sent = False
                        if (
                            asr_direct_stream[0]
                            and dg_state == "open"
                            and dg is not None
                            and asr_ready_evt.is_set()
                            and not buffered_chunks
                        ):
                            direct_sent = await _send_chunk(chunk, from_buffer=False)
                            if not direct_sent:
                                _jlog("ws_direct_stream_fallback", sid=sid)

                        if not direct_sent:
                            asr_direct_stream[0] = False
                            buffered_chunks.append(chunk)
                            dropped_now = 0
                            if len(buffered_chunks) > max_buffered_chunks:
                                dropped_now = len(buffered_chunks) - max_buffered_chunks
                                for _ in range(dropped_now):
                                    if not buffered_chunks:
                                        break
                                    buffered_chunks.popleft()
                                _jlog(
                                    "ws_audio_drop",
                                    sid=sid,
                                    dropped=dropped_now,
                                    queued=len(buffered_chunks),
                                )
                            _maybe_emit_backpressure(
                                len(buffered_chunks), dropped_now=dropped_now
                            )

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

                        ws_text_frames_in += 1
                        parent_id = _ensure_ws_parent_id()
                        if parent_id and ws_text_frames_in % _WS_FRAME_SAMPLE == 0:
                            payload_bytes = 0
                            try:
                                payload_bytes = len((ev.get("text") or "").encode("utf-8"))
                            except Exception:
                                payload_bytes = len(ev.get("text") or "")
                            _emit_debug_event(
                                "ws_frame_in",
                                phase="session",
                                parent_id=parent_id,
                                meta={
                                    "type": "text",
                                    "bytes": payload_bytes,
                                    "route": "client",
                                },
                            )

                        if t == "KeepAlive":
                            await _ws_send_json(send, make_keepalive_ack())

                        elif t == "greet":
                            _jlog("ws_greet_recv", sid=sid)
                            _ensure_bus_task_started()

                            async def _bg():
                                nonlocal greet_end_pending, greet_end_emitted
                                try:
                                    _ensure_session_ready_emitted()
                                    _emit_flow_event("greet_start", phase="greet")
                                    greet_end_pending = True
                                    greet_end_emitted = False
                                    from app.services.streaming import run_ws_greet

                                    tid = await asyncio.to_thread(run_ws_greet, sid)
                                    tid_meta: Optional[Dict[str, Any]] = None
                                    if tid:
                                        try:
                                            tid_str = str(tid)
                                        except Exception:
                                            tid_str = ""
                                        if tid_str:
                                            tid_meta = {"turn_id": tid_str}
                                    _emit_flow_event(
                                        "assistant_end",
                                        phase="greet",
                                        meta=tid_meta,
                                    )
                                    tts_state, _tts_key = _lookup_tts_state(sid, None)
                                    emit_now = False
                                    if not tts_state:
                                        emit_now = True
                                    elif isinstance(tts_state, dict):
                                        if tts_state.get("done") or tts_state.get("error"):
                                            emit_now = True
                                    if emit_now:
                                        _maybe_emit_greet_end(via="no_tts")
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
                                    greet_end_pending = False
                                    with contextlib.suppress(Exception):
                                        await _ws_send_json(
                                            send,
                                            make_error(
                                                "greet_fail", e.__class__.__name__
                                            ),
                                        )
                                    _emit_ws_error("greet_fail")
                                finally:
                                    post_greet_phase_active[0] = True

                            asyncio.create_task(_bg())

                        elif t == "Control":
                            action_raw = obj.get("action")
                            action = str(action_raw or "").strip().lower()
                            meta_payload = obj.get("meta")
                            if not isinstance(meta_payload, dict):
                                meta_payload = {}

                            if action == "ptt_down":
                                allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt(
                                    "ptt"
                                )
                                if not allow_barge:
                                    with contextlib.suppress(Exception):
                                        if allow_reason and allow_reason != "ok":
                                            _jlog(
                                                "ptt_ignored",
                                                sid=sid,
                                                by=allow_src,
                                                state=allow_state,
                                                reason=allow_reason,
                                            )
                                    continue
                                now_ts = time.time()
                                tts_active_now = allow_state == "TTS_ACTIVE"
                                _emit_transition_event(
                                    "ptt_down",
                                    meta={"during_tts": bool(tts_active_now)},
                                    phase="ptt",
                                )
                                _set_barge_pause_meta("ptt", tts_active=tts_active_now)
                                _open_turn_for_ptt(now_ts, pause_tts=tts_active_now)
                                ptt_down_emitted[0] = True
                                continue

                            if action == "ptt_up":
                                allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt(
                                    "ptt"
                                )
                                if not allow_barge:
                                    with contextlib.suppress(Exception):
                                        if allow_reason and allow_reason != "ok":
                                            _jlog(
                                                "ptt_ignored",
                                                sid=sid,
                                                by=allow_src,
                                                state=allow_state,
                                                reason=allow_reason,
                                        )
                                    continue
                                tts_active_now = allow_state == "TTS_ACTIVE"
                                _emit_transition_event(
                                    "ptt_up",
                                    meta={"during_tts": bool(tts_active_now)},
                                    phase="ptt",
                                )
                                ptt_down_emitted[0] = False
                                _note_manual_up()
                                continue

                            if action == "vad_gate_open":
                                reason_val = meta_payload.get("reason")
                                if reason_val is None:
                                    reason_val = obj.get("reason")
                                if isinstance(reason_val, str):
                                    reason = reason_val.strip().lower()
                                elif reason_val is not None:
                                    reason = str(reason_val).strip().lower()
                                else:
                                    reason = ""
                                if not reason:
                                    reason = "speech"
                                allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt(
                                    "vad"
                                )
                                if not allow_barge:
                                    with contextlib.suppress(Exception):
                                        if allow_reason and allow_reason != "ok":
                                            _jlog(
                                                "remote_vad_ignored",
                                                sid=sid,
                                                by=allow_src,
                                                state=allow_state,
                                                reason=allow_reason,
                                            )
                                    continue
                                rms_val = meta_payload.get("rms")
                                if rms_val is None:
                                    rms_val = obj.get("rms")
                                meta_out: Dict[str, Any] = {"reason": reason}
                                if rms_val is not None:
                                    try:
                                        meta_out["rms"] = float(rms_val)
                                    except Exception:
                                        pass
                                _emit_transition_event(
                                    "vad_gate_open", meta=meta_out, phase="mic"
                                )
                                extra_meta = dict(meta_out)
                                _on_local_vad_start()
                                continue

                            if action == "vad_gate_close":
                                reason_val = meta_payload.get("reason")
                                if reason_val is None:
                                    reason_val = obj.get("reason")
                                if isinstance(reason_val, str):
                                    reason = reason_val.strip().lower()
                                elif reason_val is not None:
                                    reason = str(reason_val).strip().lower()
                                else:
                                    reason = ""
                                if not reason:
                                    reason = "silence"
                                rms_val = meta_payload.get("rms")
                                if rms_val is None:
                                    rms_val = obj.get("rms")
                                meta_out: Dict[str, Any] = {"reason": reason}
                                if rms_val is not None:
                                    try:
                                        meta_out["rms"] = float(rms_val)
                                    except Exception:
                                        pass
                                _emit_transition_event(
                                    "vad_gate_close", meta=meta_out, phase="mic"
                                )
                                _on_local_vad_stop()
                                continue

                            if action == "barge_in_start":
                                if manual_feature_enabled:
                                    allow_barge, allow_src, allow_state, allow_reason = _decide_barge_attempt(
                                        "ptt"
                                    )
                                    if not allow_barge:
                                        with contextlib.suppress(Exception):
                                            if allow_reason and allow_reason != "ok":
                                                _jlog(
                                                    "manual_barge_ignored",
                                                    sid=sid,
                                                    by=allow_src,
                                                    state=allow_state,
                                                    reason=allow_reason,
                                                )
                                        continue
                                    manual_button_down[0] = True
                                    manual_turn_active[0] = True
                                    manual_commit_pending[0] = True
                                    turn_commit_mode_ref[0] = "manual"
                                    active_turn_mode_ref[0] = "manual"
                                    _note_manual_down(time.time())
                                    _ensure_confirm_closed("manual_start")
                                    buffered_bytes = _manual_buffered_bytes()
                                    _manual_log_event(
                                        "manual_barge_in_start",
                                        bytes_buffered=buffered_bytes,
                                        provider_open=dg_state == "open",
                                    )
                                    try:
                                        current_assistant_turn_ref[0] = bus.current_assistant_turn(sid)
                                    except Exception:
                                        current_assistant_turn_ref[0] = None
                                    tts_active_now = allow_state == "TTS_ACTIVE"
                                    if not ptt_down_emitted[0]:
                                        _emit_transition_event(
                                            "ptt_down",
                                            meta={"during_tts": bool(tts_active_now)},
                                            phase="ptt",
                                        )
                                        ptt_down_emitted[0] = True
                                    _set_barge_pause_meta("ptt", tts_active=tts_active_now)
                                    if not barge.is_paused():
                                        try:
                                            barge.start(
                                                confirm_ms=0,
                                                on_commit=_on_barge_commit,
                                                send_state=_send_barge_state,
                                                auto_commit=False,
                                            )
                                        except Exception:
                                            pass
                                    try:
                                        barge.commit(_send_barge_state)
                                    except Exception:
                                        manual_commit_pending[0] = False
                                continue
                            if action == "barge_in_end":
                                if manual_feature_enabled:
                                    manual_button_down[0] = False
                                    manual_turn_active[0] = False
                                    manual_commit_pending[0] = False
                                    _note_manual_up()
                                    ptt_turn_preopened[0] = False
                                    turn_commit_mode_ref[0] = "vad"
                                    buffered_bytes = _manual_buffered_bytes()
                                    _manual_log_event(
                                        "manual_barge_in_end",
                                        bytes_buffered=buffered_bytes,
                                        provider_open=dg_state == "open",
                                    )
                                    tts_active_now = _current_tts_active()
                                    if ptt_down_emitted[0]:
                                        _emit_transition_event(
                                            "ptt_up",
                                            meta={"during_tts": bool(tts_active_now)},
                                            phase="ptt",
                                        )
                                    ptt_down_emitted[0] = False
                                _push_interaction_policy(_interaction_policy_idle_snapshot())
                                continue
                            continue

                        elif t == "Configure":
                            try:
                                cfg.update(obj or {})
                                manual_feature_enabled = bool(
                                    cfg.get("feature_manual_barge_in", manual_feature_enabled)
                                )
                                manual_mode_manual_only = bool(
                                    cfg.get("barge_in_mode_manual", manual_mode_manual_only)
                                )
                                _reapply_interaction_policy()
                                ws_configured = True
                                _jlog(
                                    "ws_configure",
                                    sid=sid,
                                    ts_ms=int(time.time() * 1000),
                                    barge_in_mode_manual=manual_mode_manual_only,
                                    feature_manual_barge_in=manual_feature_enabled,
                                    auto_commit_when_ready=auto_commit_when_ready,
                                )
                                if not configure_ack_sent:
                                    greet_flag = bool(cfg.get("greet"))
                                    reset_val = cfg.get("reset", 0)
                                    try:
                                        flow_emit(
                                            session_id=sid,
                                            type="session_ready",
                                            phase="ws",
                                            who="server",
                                            meta=_with_ws_component(
                                                {
                                                    "greet": greet_flag,
                                                    "reset": reset_val,
                                                }
                                            ),
                                        )
                                    except Exception:
                                        pass
                                    await _ws_send_json(
                                        send,
                                        {
                                            "type": "ConfigureAck",
                                            "ok": True,
                                            "greet": greet_flag,
                                        },
                                    )
                                    configure_ack_sent = True
                                if not manual_feature_enabled:
                                    manual_button_down[0] = False
                                    manual_turn_active[0] = False
                                    manual_commit_pending[0] = False
                                    ptt_turn_preopened[0] = False
                                    _note_manual_up()
                                greet_seq_raw = obj.get("greet_seq")
                                greet_seq: Optional[int] = None
                                is_new_greet_seq = True
                                if greet_seq_raw is not None:
                                    try:
                                        greet_seq = int(greet_seq_raw)
                                    except Exception:
                                        greet_seq = None
                                if greet_seq is not None and (
                                    obj.get("greet") or obj.get("reset")
                                ):
                                    try:
                                        is_new_greet_seq = await _greet_seq_mark_if_new(
                                            sid, greet_seq
                                        )
                                    except Exception:
                                        is_new_greet_seq = True
                                if obj.get("reset"):
                                    if is_new_greet_seq:
                                        with contextlib.suppress(Exception):
                                            clear_greet_turn_cache(sid)
                                        with contextlib.suppress(Exception):
                                            _admin_emit and _admin_emit(
                                                "greet:reset",
                                                route="/ws/v1/chat",
                                                label="greet:reset",
                                                session_id=sid,
                                                greet_seq=greet_seq,
                                            )
                                    else:
                                        _jlog(
                                            "ws_greet_reset_skip_dup",
                                            sid=sid,
                                            greet_seq=greet_seq,
                                            via="Configure",
                                        )
                                if obj.get("greet"):
                                    if not is_new_greet_seq:
                                        _jlog(
                                            "ws_greet_skip_dup",
                                            sid=sid,
                                            greet_seq=greet_seq,
                                            via="Configure",
                                        )
                                        continue
                                    _jlog(
                                        "ws_greet_recv",
                                        sid=sid,
                                        via="Configure",
                                        greet_seq=greet_seq,
                                    )
                                    _ensure_bus_task_started()

                                    async def _bg2():
                                        nonlocal greet_end_pending, greet_end_emitted
                                        try:
                                            _ensure_session_ready_emitted()
                                            _emit_flow_event("greet_start", phase="greet")
                                            greet_end_pending = True
                                            greet_end_emitted = False
                                            from app.services.streaming import run_ws_greet

                                            tid = await asyncio.to_thread(run_ws_greet, sid)
                                            tid_meta: Optional[Dict[str, Any]] = None
                                            if tid:
                                                try:
                                                    tid_str = str(tid)
                                                except Exception:
                                                    tid_str = ""
                                                if tid_str:
                                                    tid_meta = {"turn_id": tid_str}
                                            _emit_flow_event(
                                                "assistant_end",
                                                phase="greet",
                                                meta=tid_meta,
                                            )
                                            tts_state, _tts_key = _lookup_tts_state(sid, None)
                                            emit_now = False
                                            if not tts_state:
                                                emit_now = True
                                            elif isinstance(tts_state, dict):
                                                if tts_state.get("done") or tts_state.get("error"):
                                                    emit_now = True
                                            if emit_now:
                                                _maybe_emit_greet_end(via="no_tts")
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
                                            greet_end_pending = False
                                            with contextlib.suppress(Exception):
                                                await _ws_send_json(
                                                    send,
                                                    make_error(
                                                        "greet_fail",
                                                        e.__class__.__name__,
                                                    ),
                                                )
                                            _emit_ws_error("greet_fail")
                                        finally:
                                            post_greet_phase_active[0] = True

                                    asyncio.create_task(_bg2())
                            except Exception as ex:
                                await _handle_startup_failure(ex)
                                return

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
                                _emit_ws_error("payload_too_large")
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

                            _ensure_bus_task_started()

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
                                    _emit_ws_error("user_fail")

                            asyncio.create_task(_bg_user())

                        elif t == "CloseStream":
                            _jlog("ws_close_stream", sid=sid)
                            manual_turn_active[0] = False
                            manual_button_down[0] = False
                            _note_manual_up()
                            ptt_turn_preopened[0] = False
                            turn_stream_committed[0] = False
                            _cancel_asr_stream_activation()
                            _cancel_asr_not_ready_timeout()

                            # Always define this first so later 'if synthetic_emitted' is safe
                            synthetic_emitted = False
                            asr_ready_wait_timed_out = False
                            force_end_due_to_asr_timeout = False

                            async def _await_close_asr_ready(
                                timeout: float,
                                *,
                                mark_timeout: bool = True,
                                log_cb: Optional[Callable[[], None]] = None,
                            ) -> bool:
                                nonlocal asr_ready_wait_timed_out, force_end_due_to_asr_timeout
                                if asr_ready_evt is None:
                                    return False
                                if asr_ready_evt.is_set():
                                    return True
                                if asr_ready_wait_timed_out:
                                    return False
                                if timeout <= 0:
                                    return False
                                try:
                                    await asyncio.wait_for(
                                        asr_ready_evt.wait(), timeout=timeout
                                    )
                                    return True
                                except asyncio.TimeoutError:
                                    if mark_timeout:
                                        asr_ready_wait_timed_out = True
                                        force_end_due_to_asr_timeout = True
                                    if log_cb is not None:
                                        with contextlib.suppress(Exception):
                                            log_cb()
                                    return False

                            if callable(final_guard_local_vad_ref[0]):
                                with contextlib.suppress(Exception):
                                    final_guard_local_vad_ref[0]("stop")
                            _on_local_vad_stop()

                            _ensure_confirm_closed("close_stream")

                            if buf.is_empty():
                                # Empty turn closure; synthesize ids + reset final tracking.
                                turn_id_ref[0] = buf.turn_seq + 1
                                final_seen[0] = False
                                _reset_turn_metrics(time.time())
                                with contextlib.suppress(Exception):
                                    turn_payload: Dict[str, Any] = {
                                        "sid": sid,
                                        "turn_id": turn_id_ref[0],
                                        "first_bytes": 0,
                                        "empty_turn": True,
                                        "commit_mode": "programmatic",
                                        "auto_commit": False,
                                        "ts_ms": int(time.time() * 1000),
                                    }
                                    turn_payload.update(
                                        make_source_meta(
                                            _resolve_turn_open_source("programmatic"),
                                            gates=_current_gate_snapshot(),
                                            evidence=_make_barge_evidence(),
                                        )
                                    )
                                    _jlog("turn_start", **turn_payload)

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
                                    await _await_close_asr_ready(1.2, mark_timeout=False)

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
                                    await _await_close_asr_ready(asr_ready_wait_s)

                                # Flush any staged audio first
                                await _flush_buffered_chunks()

                                # Give ASR a brief chance to be "ready", then flush again
                                await _await_close_asr_ready(1.0, mark_timeout=False)

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
                                    if asr_ready_evt is not None:
                                        ready_now = await _await_close_asr_ready(
                                            asr_ready_wait_s,
                                            log_cb=lambda: _jlog(
                                                "ws_close_dg_ready_timeout", sid=sid
                                            ),
                                        )
                                        if not ready_now:
                                            readiness_timeout = True
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
                                                os.getenv("ASR_FINAL_GRACE_S", "0.80")
                                            )
                                        )  # ~800 ms grace
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
                                    asr_direct_stream[0] = False
                                    _relay_task = rx_task
                                    rx_task = None
                                    if _relay_task:
                                        drain_wait_s = 0.15
                                        with contextlib.suppress(Exception):
                                            drain_wait_s = float(
                                                os.getenv(
                                                    "DG_FINAL_DRAIN_WAIT_S",
                                                    "0.15",
                                                )
                                            )
                                        drain_wait_s = max(0.0, drain_wait_s)
                                        if drain_wait_s > 0 and not _relay_task.done():
                                            try:
                                                await asyncio.wait_for(
                                                    asyncio.shield(_relay_task),
                                                    timeout=drain_wait_s,
                                                )
                                            except asyncio.TimeoutError:
                                                pass
                                            except Exception:
                                                pass
                                        if not _relay_task.done():
                                            _relay_task.cancel()
                                            with contextlib.suppress(
                                                asyncio.CancelledError, Exception
                                            ):
                                                await _relay_task
                                    if not final_seen[0]:
                                        recovered_text = _recover_transcript_from_client(dg)
                                        if recovered_text:
                                            pending_final_texts[turn_id] = recovered_text
                                            last_user_final_text[0] = recovered_text
                                            if recovered_text:
                                                last_partial_text[0] = recovered_text
                                    dg = None
                                    with contextlib.suppress(Exception):
                                        asr_ready_evt.clear()
                                    asr_direct_stream[0] = False
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
                                should_emit = (not assistant_speaking[0]) and (
                                    no_audio_window_s <= 0
                                    or (
                                        elapsed is not None
                                        and elapsed >= no_audio_window_s
                                    )
                                )
                                if should_emit:
                                    _emit_no_audio_alert("close_stream")

                            if synthetic_emitted:
                                # Reset so the next turn starts fresh even if no audio chunk arrives.
                                final_seen[0] = False

                            if force_end_due_to_asr_timeout:
                                _jlog(
                                    "session_force_end",
                                    sid=sid,
                                    reason="user_end_waiting_asr",
                                )
                                try:
                                    flow_emit(
                                        session_id=sid,
                                        type="session_force_end",
                                        phase="session",
                                        who="server",
                                        meta=_with_ws_component(
                                            {"reason": "user_end_waiting_asr"}
                                        ),
                                    )
                                except Exception:
                                    pass
                                await _safe_close(4000, "user_end_waiting_asr")
                                return

                            if barge.is_paused():
                                _ensure_confirm_closed("cleanup")
                                if not manual_button_down[0]:
                                    with contextlib.suppress(Exception):
                                        barge.cancel(_send_barge_state)
                                await asyncio.sleep(0)

                            if ws_configured and not turn_stream_committed[0]:
                                if manual_commit_pending[0] and not assistant_speaking[0]:
                                    _on_barge_commit("manual")
                                elif _can_auto_commit_now():
                                    _on_barge_commit("auto")

                            active_turn_mode_ref[0] = "vad"
                            turn_commit_mode_ref[0] = "vad"

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
                        _emit_ws_error("bad_message")
                else:
                    # websocket.receive without text/bytes
                    pass
            else:
                # Other ASGI events are ignored
                pass

    finally:
        if flow_session_open_emitted and not flow_session_close_emitted:
            _emit_flow_event("session_close", phase="session")
            flow_session_close_emitted = True
        _ensure_confirm_closed("shutdown")
        _cancel_confirm_timeout()
        with contextlib.suppress(Exception):
            _cancel_no_audio_watch()
        _cancel_asr_stream_activation()
        _cancel_asr_not_ready_timeout()
        task = vad_apply_task[0]
        if task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.cancel()
                await task
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
        with contextlib.suppress(Exception):
            _BARGE_EVENT_STATE.pop(sid, None)
        # Clean up safely; never raise in cleanup
        with contextlib.suppress(Exception):
            if rx_task:
                rx_task.cancel()
                await rx_task
        with contextlib.suppress(Exception):
            if dg is not None:
                await dg.close(wait_for_final=False)
        if bus_task:
            with contextlib.suppress(Exception):
                bus_task.cancel()
                await bus_task
        with contextlib.suppress(Exception):
            ping_task.cancel()
            await ping_task
        if voice_metrics_registered:
            with contextlib.suppress(Exception):
                await _unregister_voice_metrics_subscriber(conn_id)
        for task in list(confirm_timeout_cancelled):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        confirm_timeout_cancelled.clear()
        with contextlib.suppress(Exception):
            await _safe_close(1000, "normal_shutdown")


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
    except Exception as ex:
        detail = str(ex).strip() or "initial ready failed"
        with contextlib.suppress(Exception):
            flow_emit(
                session_id=sid,
                type="ws_error",
                phase="transport",
                who="server",
                meta=_with_ws_component(
                    {
                    "where": "initial_ready",
                    "cause": ex.__class__.__name__,
                    "msg": str(ex)[:300],
                    }
                ),
            )
        with contextlib.suppress(Exception):
            await websocket.send_text(
                _dumps(
                    {
                        "type": "Error",
                        "code": "INITIAL_READY_FAILED",
                        "detail": detail[:200],
                    }
                )
            )
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


def _admin_ws_close_breadcrumb(session_id: str, code: int = 1000, reason: str = ''):
    try:
        from app.api_v1.admin import _emit as _admin_emit
        _admin_emit('ws_close', session_id=session_id, code=str(code), reason=reason)
    except Exception:
        pass

