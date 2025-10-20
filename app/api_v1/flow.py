from __future__ import annotations

import copy
import gzip
import hashlib
import io
import ipaddress
import json
import re
import zipfile
from datetime import datetime, timezone
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    request,
    session as flask_session,
    stream_with_context,
)

from app.flow import FlowStore
from app.flow.trace import (
    assemble_ws_frames,
    slice_client_console_for_session,
    slice_server_log_for_session,
)
from app.flow.catalog import FLOW_EVENT_CATALOG
from app.security_state import get_user
from app.utils.admin import is_admin_email

bp = Blueprint("flow", __name__)

_CLIENT_BREADCRUMB_WINDOW_SEC = 60.0
_CLIENT_BREADCRUMB_LIMIT = 30
_CLIENT_BREADCRUMB_LOCK = threading.Lock()
_CLIENT_BREADCRUMB_HITS: Dict[str, Deque[float]] = {}


def _normalize_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _require_admin() -> None:
    email = (
        (flask_session.get("user") or {}).get("email")
        or flask_session.get("email")
        or request.headers.get("X-User-Email")
        or (get_user() or "")
    )
    if not is_admin_email((email or "").strip().lower()):
        abort(403)


def _parse_int(value: Optional[str], *, name: str, minimum: Optional[int] = None) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        abort(400, description=f"Invalid {name}")
    if minimum is not None and parsed < minimum:
        abort(400, description=f"{name} must be >= {minimum}")
    return parsed


def _parse_levels(raw: Optional[str]) -> Iterable[str]:
    if raw is None:
        return ("flow", "transition", "debug")
    levels: List[str] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if piece not in levels:
            levels.append(piece)
    return levels


def _parse_bool(value: Optional[str], *, default: bool = True) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _too_large_response() -> Response:
    response = jsonify({"error": "export_too_large"})
    response.status_code = 413
    return response


def _extract_policy_snapshot(config_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(config_payload, Mapping):
        return None

    def _normalize_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        try:
            text = str(value)
        except Exception:
            return None
        text = text.strip()
        return text or None

    def _normalize_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except Exception:
                return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(float(text))
            except Exception:
                return None
        return None

    def _normalize_bool(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return None

    def _parse_barge_policy(policy: Any) -> Dict[str, Any]:
        if not isinstance(policy, Mapping):
            return {}
        suppress_value = _normalize_str(policy.get("suppress_during_tts"))
        hold_value = _normalize_int(policy.get("post_tts_hold_ms"))
        allow_value = _normalize_bool(policy.get("allow_ptt"))
        require_value = _normalize_bool(policy.get("require_asr_evidence"))

        snapshot: Dict[str, Any] = {}
        if suppress_value is not None:
            snapshot["suppress_during_tts"] = suppress_value
        if hold_value is not None:
            snapshot["post_tts_hold_ms"] = hold_value
        if allow_value is not None:
            snapshot["allow_ptt"] = allow_value
        if require_value is not None:
            snapshot["require_asr_evidence"] = require_value
        return snapshot

    def _coerce_snapshot(candidate: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(candidate, Mapping):
            return None

        result: Dict[str, Any] = {}

        voice_runtime = candidate.get("voice_runtime")
        if isinstance(voice_runtime, Mapping):
            barge_data = _parse_barge_policy(voice_runtime.get("barge_in"))
            if barge_data:
                result.setdefault("voice_runtime", {})["barge_in"] = barge_data

        asr_policy = candidate.get("asr")
        if isinstance(asr_policy, Mapping):
            vad_policy = asr_policy.get("vad")
            if isinstance(vad_policy, Mapping):
                vad_enabled = _normalize_bool(vad_policy.get("enabled"))
                if vad_enabled is not None:
                    result.setdefault("asr", {})["vad"] = {"enabled": vad_enabled}

        return result or None

    explicit_snapshot = _coerce_snapshot(config_payload.get("policy_snapshot"))
    if explicit_snapshot:
        return explicit_snapshot

    interaction_policy = config_payload.get("interaction_policy")
    if not isinstance(interaction_policy, Mapping):
        return None

    candidate_payload: Dict[str, Any] = {}
    voice_runtime = interaction_policy.get("voice_runtime")
    if isinstance(voice_runtime, Mapping):
        candidate_payload.setdefault("voice_runtime", {})["barge_in"] = voice_runtime.get("barge_in")

    asr_policy = interaction_policy.get("asr")
    if isinstance(asr_policy, Mapping):
        candidate_payload.setdefault("asr", {})["vad"] = asr_policy.get("vad")

    return _coerce_snapshot(candidate_payload)


DEFAULT_HANDOFF_PROMPT = (
    "Analyze the redacted conversational flow; identify root cause(s), evidence (event IDs), "
    "smallest viable fix, and validation steps."
)

_HANDOFF_TELEMETRY_SCHEMAS = ["barge_decision.v1"]

_TEXT_EXACT_KEYS = {
    "text",
    "text_preview",
    "user_text",
    "assistant_text",
    "tts_text",
    "raw_text",
    "message",
    "body",
    "delta",
    "response",
    "prompt",
    "content",
}
_TEXT_SUFFIXES = ("_text", "_message", "_body", "_delta", "_content")
_TEXT_SUBSTRINGS = ("transcript", "utterance")
_DEVICE_KEYWORDS = ("device", "hardware", "microphone", "speaker", "headset")
_DEVICE_CLASS_MAP = {
    "iphone": "iphone",
    "ipad": "ipad",
    "macbook": "mac",
    "imac": "mac",
    "mac": "mac",
    "pixel": "android",
    "android": "android",
    "galaxy": "android",
    "oneplus": "android",
    "samsung": "android",
    "windows": "windows",
    "surface": "windows",
    "pc": "windows",
    "thinkpad": "windows",
    "linux": "linux",
}

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_GENERIC_TOKEN_PATTERN = re.compile(
    r"(?i)\b(?:bearer|token|secret|key|api[_-]?key|authorization)[\s:=]+([A-Za-z0-9._-]{12,})"
)
_OPENAI_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{8,}\b")
_IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _iter_session_events(
    store: FlowStore,
    session_id: str,
    levels: Iterable[str],
    since_ms: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    cursor = since_ms if since_ms is not None else 0
    while True:
        chunk = store.list(
            session_id=session_id,
            since_ms=cursor,
            limit=500,
            levels=levels,
            expand="all",
        )
        events = chunk.get("events", [])
        if not events:
            break
        for event in events:
            if isinstance(event, dict):
                yield event
        next_since = chunk.get("next_since_ms")
        if not isinstance(next_since, int) or next_since <= cursor:
            break
        cursor = next_since


def _coerce_levels(value: Any) -> List[str]:
    if value is None:
        return list(_parse_levels(None))
    if isinstance(value, str):
        return list(_parse_levels(value))
    levels: List[str] = []
    if isinstance(value, Iterable):
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if text not in levels:
                levels.append(text)
    if not levels:
        return list(_parse_levels(None))
    if "flow" not in levels:
        levels.insert(0, "flow")
    return levels


def _check_client_breadcrumb_rate(session_id: str) -> bool:
    now = time.monotonic()
    with _CLIENT_BREADCRUMB_LOCK:
        queue = _CLIENT_BREADCRUMB_HITS.setdefault(session_id, deque())
        cutoff = now - _CLIENT_BREADCRUMB_WINDOW_SEC
        while queue and queue[0] <= cutoff:
            queue.popleft()
        if len(queue) >= _CLIENT_BREADCRUMB_LIMIT:
            return False
        queue.append(now)
        return True


def _should_mask_text(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in _TEXT_EXACT_KEYS:
        return True
    if key_lower.endswith("_preview"):
        return True
    if any(key_lower.endswith(suffix) for suffix in _TEXT_SUFFIXES):
        return True
    if any(fragment in key_lower for fragment in _TEXT_SUBSTRINGS):
        return True
    if "prompt" in key_lower and "token" not in key_lower:
        return True
    return False


def _should_classify_device(key: str) -> bool:
    key_lower = key.lower()
    return any(fragment in key_lower for fragment in _DEVICE_KEYWORDS)


def _mask_text(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:8]
    return f"[redacted len={len(value)} sha1_8={digest}]"


def _classify_device_label(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", value or "").strip().lower()
    if not cleaned:
        return ""
    tokens = [token for token in cleaned.split() if token]
    for token in tokens:
        mapped = _DEVICE_CLASS_MAP.get(token)
        if mapped:
            return mapped
    base = tokens[0] if tokens else ""
    return base[:12] if base else "unknown"


def _redact_string(key: str, value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    key_lower = key.lower()
    if key_lower in {"sha1", "sha1_8", "md5"}:
        return text
    if key_lower in {"bytes", "len", "length"}:
        return text
    if _should_classify_device(key_lower):
        return _classify_device_label(text)
    if _should_mask_text(key_lower):
        return _mask_text(text)
    return text


def _redact_node(value: Any, *, key_path: Optional[tuple[str, ...]] = None) -> Any:
    key_path = key_path or ()
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_value is None:
                continue
            child_key_str = str(child_key)
            sanitized[child_key_str] = _redact_node(
                child_value,
                key_path=key_path + (child_key_str.lower(),),
            )
        return sanitized
    if isinstance(value, list):
        return [
            _redact_node(item, key_path=key_path)
            for item in value
        ]
    if isinstance(value, str):
        last_key = key_path[-1] if key_path else ""
        return _redact_string(last_key, value)
    return value


def _redact_mapping(mapping: Any) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in mapping.items():
        if value is None:
            continue
        key_str = str(key)
        sanitized[key_str] = _redact_node(value, key_path=(key_str.lower(),))
    return sanitized


def _classify_payload_category(path_value: Any) -> Optional[str]:
    if not isinstance(path_value, str):
        return None
    lowered = path_value.lower()
    if "llm" in lowered:
        return "llm"
    if "tts" in lowered:
        return "tts"
    if "dg" in lowered or "deepgram" in lowered or "asr" in lowered:
        return "asr"
    return None


def _redact_payload_sig_meta(meta: Any) -> Dict[str, Any]:
    source = meta if isinstance(meta, dict) else {}
    sanitized: Dict[str, Any] = {}
    bytes_value = source.get("bytes")
    try:
        if bytes_value is not None:
            sanitized["bytes"] = int(bytes_value)
    except (TypeError, ValueError):
        pass
    sha_value = source.get("sha1_8")
    if isinstance(sha_value, str) and sha_value:
        sanitized["sha1_8"] = sha_value[:8]
    category = _classify_payload_category(source.get("path"))
    if category:
        sanitized["category"] = category
    return sanitized


def _redact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    clone = copy.deepcopy(event)
    original_meta = clone.get("meta")
    if clone.get("type") == "payload_sig":
        clone["meta"] = _redact_payload_sig_meta(original_meta)
    else:
        clone["meta"] = _redact_mapping(original_meta)
    if isinstance(clone.get("children"), list):
        clone["children"] = [
            _redact_event(child)
            for child in clone["children"]
            if isinstance(child, dict)
        ]
    if isinstance(clone.get("batches"), list):
        clone["batches"] = [
            _redact_node(batch, key_path=("batch",))
            if isinstance(batch, dict)
            else batch
            for batch in clone["batches"]
        ]
    return clone


def _hash_token_value(value: str, prefix: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"[{prefix}:{digest}]"


def _scrub_text(text: str) -> str:
    if not text:
        return text

    def _email_repl(match: re.Match[str]) -> str:
        return _hash_token_value(match.group(0).lower(), "email")

    text = _EMAIL_PATTERN.sub(_email_repl, text)

    def _token_repl(match: re.Match[str]) -> str:
        token = match.group(1)
        hashed = _hash_token_value(token, "token")
        return match.group(0).replace(token, hashed)

    text = _GENERIC_TOKEN_PATTERN.sub(_token_repl, text)

    def _openai_repl(match: re.Match[str]) -> str:
        token = match.group(0)
        return _hash_token_value(token, "token")

    text = _OPENAI_TOKEN_PATTERN.sub(_openai_repl, text)

    def _ip_repl(match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            ip_obj = ipaddress.ip_address(value)
        except ValueError:
            return value
        if getattr(ip_obj, "version", None) == 4:
            if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified:
                return value
            return _hash_token_value(value, "ip")
        return value

    text = _IPV4_PATTERN.sub(_ip_repl, text)
    return text


def _scrub_node(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, child in value.items():
            try:
                key_str = str(key)
            except Exception:
                key_str = repr(key)
            sanitized[key_str] = _scrub_node(child)
        return sanitized
    if isinstance(value, list):
        return [_scrub_node(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_node(item) for item in value)
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_event(event: Dict[str, Any]) -> Dict[str, Any]:
    clone = copy.deepcopy(event)
    for key, value in list(clone.items()):
        if isinstance(value, dict):
            clone[key] = _scrub_node(value)
        elif isinstance(value, list):
            clone[key] = [_scrub_node(item) for item in value]
        elif isinstance(value, tuple):
            clone[key] = tuple(_scrub_node(item) for item in value)
        elif isinstance(value, str):
            clone[key] = _scrub_text(value)
    return clone


def _scrub_gzip_bytes(data: Optional[bytes]) -> Optional[bytes]:
    if not data:
        return data
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError):
        return data
    sanitized = _scrub_text(text)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz_handle:
        gz_handle.write(sanitized.encode("utf-8"))
    return buffer.getvalue()


def _decode_gzip_ndjson(data: Optional[bytes]) -> List[Dict[str, Any]]:
    if not data:
        return []
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError):
        return []
    entries: List[Dict[str, Any]] = []
    for line in text.splitlines():
        chunk = line.strip()
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def _strip_none_values(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


_TIMELINE_FLOW_TYPES = {
    "tts_start",
    "tts_end",
    "barge_in",
    "barge_pause",
    "barge_resume",
    "session_force_end",
}
_TIMELINE_FLOW_PREFIXES = ("client_audio_",)
_TIMELINE_SERVER_EVENTS = {
    "barge_decision",
    "asr_vad_state",
    "ptt_open",
    "ptt_close",
    "vad_arm",
    "session_force_end",
}
_TIMELINE_SERVER_PREFIXES = ("client_audio_",)


def _build_timeline_payload(
    *,
    session_id: str,
    generated_at: str,
    flow_events: List[Dict[str, Any]],
    server_events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    flow_timeline: List[Dict[str, Any]] = []
    for event in flow_events:
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("type") or "")
        include = event_type in _TIMELINE_FLOW_TYPES or any(
            event_type.startswith(prefix) for prefix in _TIMELINE_FLOW_PREFIXES
        )
        if not include:
            continue
        entry: Dict[str, Any] = {
            "id": event.get("id"),
            "type": event_type,
            "phase": event.get("phase"),
            "who": event.get("who"),
            "t_rel_ms": event.get("t_rel_ms"),
            "turn_id": event.get("turn_id"),
        }
        meta_value = event.get("meta")
        if isinstance(meta_value, Mapping) and meta_value:
            entry["meta"] = dict(meta_value)
        flow_timeline.append(_strip_none_values(entry))

    flow_timeline.sort(key=lambda item: (item.get("t_rel_ms") or 0, item.get("id") or ""))

    server_timeline: List[Dict[str, Any]] = []
    for record in server_events:
        if not isinstance(record, Mapping):
            continue
        event_name = str(record.get("event") or "")
        include = event_name in _TIMELINE_SERVER_EVENTS or any(
            event_name.startswith(prefix) for prefix in _TIMELINE_SERVER_PREFIXES
        )
        if not include:
            continue
        entry = {
            key: value
            for key, value in record.items()
            if key not in {"session_id", "sid"}
        }
        server_timeline.append(entry)

    server_timeline.sort(key=lambda item: (item.get("ts_ms") or 0, item.get("event") or ""))

    if not flow_timeline and not server_timeline:
        return None

    payload: Dict[str, Any] = {
        "session_id": session_id,
        "generated_at": generated_at,
    }
    if flow_timeline:
        payload["flow_events"] = flow_timeline
    if server_timeline:
        payload["server_events"] = server_timeline
    return payload


@bp.post("/flow/breadcrumb")
def flow_breadcrumb():
    payload = request.get_json(silent=True) or {}

    session_id = _normalize_str(
        payload.get("session_id") or payload.get("sid")
    )
    meta_payload = payload.get("meta")
    if not session_id and isinstance(meta_payload, dict):
        session_id = _normalize_str(
            meta_payload.get("session_id") or meta_payload.get("sid")
        )
    if not session_id:
        abort(400, description="session_id is required")

    event_name = _normalize_str(payload.get("event"))
    if not event_name:
        abort(400, description="event is required")

    if meta_payload is None:
        meta: Dict[str, Any] = {}
    elif isinstance(meta_payload, dict):
        meta = dict(meta_payload)
    else:
        abort(400, description="meta must be an object")

    ts_value = payload.get("ts_ms")
    if ts_value is not None:
        try:
            ts_ms = int(ts_value)
        except (TypeError, ValueError):
            abort(400, description="ts_ms must be an integer")
        meta["ts_ms"] = ts_ms

    if "event" not in meta:
        meta["event"] = event_name

    if not _check_client_breadcrumb_rate(session_id):
        abort(429, description="rate limit exceeded")

    event_type = event_name if event_name.startswith("client_") else f"client_{event_name}"

    store = FlowStore()
    store.emit(
        session_id=session_id,
        level="debug",
        phase="client",
        type_=event_type,
        who="client",
        meta=meta,
    )

    return ("", 204)


@bp.get("/flow/catalog")
def flow_catalog():
    _require_admin()
    return jsonify({"catalog": FLOW_EVENT_CATALOG})


@bp.get("/flow/sessions")
def flow_sessions():
    _require_admin()

    search_value = _normalize_str(request.args.get("q") or request.args.get("query"))
    limit_value = _parse_int(request.args.get("limit"), name="limit", minimum=0)

    store = FlowStore()
    limit = limit_value if limit_value is not None else 50
    sessions = store.sessions(query=search_value, limit=limit)
    return jsonify({"sessions": sessions})


@bp.get("/flow/trace")
def flow_trace():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    if not session_id:
        abort(400, description="session_id is required")

    since_value = _parse_int(request.args.get("since_ms"), name="since_ms", minimum=0)
    limit_value = _parse_int(request.args.get("limit"), name="limit", minimum=1)
    if limit_value is None:
        limit_value = 200
    limit_value = min(limit_value, 1000)

    levels = _parse_levels(request.args.get("levels"))
    expand = request.args.get("expand") or "flow"

    store = FlowStore()
    payload = store.list(
        session_id=session_id,
        since_ms=since_value,
        limit=limit_value,
        levels=levels,
        expand=expand,
    )
    return jsonify(payload)


@bp.get("/flow/event")
def flow_event():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    event_id = _normalize_str(request.args.get("id"))
    if not session_id or not event_id:
        abort(400, description="session_id and id are required")

    store = FlowStore()
    event = store.get(session_id, event_id)
    if not event:
        abort(404)
    return jsonify(event)


@bp.get("/flow/export.ndjson")
def flow_export_ndjson():
    _require_admin()

    session_id = _normalize_str(request.args.get("session_id"))
    if not session_id:
        abort(400, description="session_id is required")

    since_value = _parse_int(request.args.get("since_ms"), name="since_ms", minimum=0)
    levels = list(_parse_levels(request.args.get("levels")))
    redacted = _parse_bool(request.args.get("redacted"), default=True)
    scrub_requested = _parse_bool(request.args.get("pii_scrub"), default=False)

    store = FlowStore()

    def _generate() -> Iterable[str]:
        for event in _iter_session_events(store, session_id, levels, since_value):
            payload = _redact_event(event) if redacted else copy.deepcopy(event)
            if scrub_requested:
                payload = _scrub_event(payload)
            yield json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"

    response = Response(stream_with_context(_generate()), mimetype="application/x-ndjson")
    response.headers["X-Flow-Redacted"] = "1" if redacted else "0"
    if scrub_requested:
        response.headers["X-Flow-PII-Scrubbed"] = "1"
    return response


@bp.route("/flow/handoff", methods=["GET", "POST"])
def flow_handoff():
    _require_admin()

    payload: Dict[str, Any] = {}
    query_args = request.args or {}

    if request.method == "POST":
        raw_payload = request.get_json(silent=True)
        if isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            payload = {}

    def _payload_value(key: str) -> Any:
        if request.method != "POST":
            return None
        return payload.get(key)

    session_id = _normalize_str(
        _payload_value("session_id") or query_args.get("session_id")
    )
    if not session_id:
        abort(400, description="session_id is required")

    levels_value = _payload_value("levels")
    if levels_value is None:
        levels_value = query_args.get("levels")
    levels = _coerce_levels(levels_value)

    prompt_value = _payload_value("prompt")
    if prompt_value is None:
        prompt_value = query_args.get("prompt")
    prompt_text = _normalize_str(prompt_value) or DEFAULT_HANDOFF_PROMPT

    raw_options: Dict[str, Any] = {}
    if request.method == "POST":
        options_payload = payload.get("options")
        if isinstance(options_payload, dict):
            raw_options = options_payload

    mode_candidate: Optional[str] = None
    if request.method == "POST":
        mode_payload = payload.get("mode")
        if isinstance(mode_payload, str):
            mode_candidate = mode_payload
    options_mode = raw_options.get("mode") if raw_options else None
    if isinstance(options_mode, str):
        mode_candidate = options_mode
    if mode_candidate is None:
        query_mode = query_args.get("mode")
        if isinstance(query_mode, str):
            mode_candidate = query_mode
    mode_text = _normalize_str(mode_candidate) or "redacted"
    mode = mode_text.lower()
    if mode not in {"redacted", "full"}:
        mode = "redacted"
    is_full = mode == "full"

    include_options = raw_options.get("include") if raw_options else {}
    if not isinstance(include_options, dict):
        include_options = {}
    privacy_options = raw_options.get("privacy") if raw_options else {}
    if not isinstance(privacy_options, dict):
        privacy_options = {}
    limits_options = raw_options.get("limits") if raw_options else {}
    if not isinstance(limits_options, dict):
        limits_options = {}

    raw_pii_scrub = privacy_options.get("pii_scrub")
    if isinstance(raw_pii_scrub, str):
        pii_scrub_requested = raw_pii_scrub.strip().lower() in {"1", "true", "yes", "on"}
    else:
        pii_scrub_requested = bool(raw_pii_scrub)

    max_bytes: Optional[int] = None
    if "max_bytes" in limits_options:
        try:
            max_bytes = int(limits_options.get("max_bytes"))
        except (TypeError, ValueError):
            abort(400, description="limits.max_bytes must be an integer")
        if max_bytes < 0:
            abort(400, description="limits.max_bytes must be >= 0")

    store = FlowStore()
    snapshot = store.snapshot(session_id=session_id, levels=levels, expand="all")
    events = snapshot.events
    event_count = len(events)

    generated_at = datetime.now(timezone.utc).isoformat()

    def _to_ndjson_bytes(items: Iterable[Dict[str, Any]]) -> bytes:
        lines = [
            json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            for item in items
        ]
        ndjson_text = "\n".join(lines)
        if ndjson_text:
            ndjson_text += "\n"
        return ndjson_text.encode("utf-8")

    if not is_full:
        redacted_events = [_redact_event(event) for event in events]
        if pii_scrub_requested:
            redacted_events = [_scrub_event(event) for event in redacted_events]
        ndjson_bytes = _to_ndjson_bytes(redacted_events)
        payload_sha = hashlib.sha1(ndjson_bytes).hexdigest()
        meta_payload = {
            "session_id": session_id,
            "levels": levels,
            "generated_at": generated_at,
            "event_count": len(redacted_events),
            "payload_sig": {"bytes": len(ndjson_bytes), "sha1_8": payload_sha[:8]},
            "mode": mode,
        }
        if pii_scrub_requested:
            meta_payload["privacy"] = {"pii_scrub": True}

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("flow.ndjson", ndjson_bytes)
            archive.writestr("prompt.txt", prompt_text)
            archive.writestr(
                "meta.json",
                json.dumps(meta_payload, ensure_ascii=False, indent=2),
            )
        zip_bytes = buffer.getvalue()
        if max_bytes is not None and len(zip_bytes) > max_bytes:
            return _too_large_response()
    else:
        export_events = [copy.deepcopy(event) for event in events]
        if pii_scrub_requested:
            export_events = [_scrub_event(event) for event in export_events]
        event_bytes = _to_ndjson_bytes(export_events)
        gzip_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=gzip_buffer, mode="wb") as gz_handle:
            gz_handle.write(event_bytes)
        events_gz_bytes = gzip_buffer.getvalue()

        config_payload = snapshot.config if isinstance(snapshot.config, dict) else {}
        policy_snapshot = _extract_policy_snapshot(config_payload)
        try:
            config_text = json.dumps(
                config_payload, ensure_ascii=False, indent=2, sort_keys=True
            )
        except TypeError:
            config_text = json.dumps({}, ensure_ascii=False, indent=2)
        config_bytes = config_text.encode("utf-8")

        prompt_bytes = prompt_text.encode("utf-8")

        include_ws_requested = bool(include_options.get("ws"))
        include_logs_requested = bool(include_options.get("logs"))

        server_log_bytes_raw = slice_server_log_for_session(session_id)
        if not server_log_bytes_raw:
            server_log_bytes_raw = gzip.compress(b"")
        server_log_bytes = (
            _scrub_gzip_bytes(server_log_bytes_raw)
            if pii_scrub_requested
            else server_log_bytes_raw
        )
        server_log_entries = _decode_gzip_ndjson(server_log_bytes)

        timeline_payload = _build_timeline_payload(
            session_id=session_id,
            generated_at=generated_at,
            flow_events=export_events,
            server_events=server_log_entries,
        )
        timeline_bytes: Optional[bytes] = None
        if timeline_payload:
            timeline_bytes = json.dumps(
                timeline_payload, ensure_ascii=False, indent=2
            ).encode("utf-8")

        include_meta: Dict[str, Any] = {}
        privacy_meta: Dict[str, Any] = {}
        for key, value in privacy_options.items():
            key_str = str(key)
            if isinstance(value, bool):
                privacy_meta[key_str] = value
            elif isinstance(value, str):
                privacy_meta[key_str] = value.strip() or value
        if pii_scrub_requested and "pii_scrub" not in privacy_meta:
            privacy_meta["pii_scrub"] = True
        limits_meta: Dict[str, Any] = {}
        if max_bytes is not None:
            limits_meta["max_bytes"] = max_bytes

        def _manifest_entry(
            path: str,
            data: bytes,
            *,
            content_type: Optional[str] = None,
            description: Optional[str] = None,
        ) -> Dict[str, Any]:
            digest = hashlib.sha1(data).hexdigest()
            entry: Dict[str, Any] = {
                "path": path,
                "bytes": len(data),
                "sha1": digest,
                "sha1_first8": digest[:8],
            }
            if content_type:
                entry["content_type"] = content_type
            if description:
                entry["description"] = description
            return entry

        optional_files: List[Tuple[str, bytes, Optional[str], Optional[str]]] = []

        ws_bytes = None
        if include_ws_requested:
            ws_bytes = assemble_ws_frames(session_id)
            if ws_bytes and pii_scrub_requested:
                ws_bytes = _scrub_gzip_bytes(ws_bytes)
            if ws_bytes:
                optional_files.append(
                    (
                        "ws/frames.ndjson.gz",
                        ws_bytes,
                        "application/x-ndjson+gzip",
                        "Sampled WebSocket frames",
                    )
                )

        client_log_bytes = None
        if include_logs_requested:
            client_log_bytes = slice_client_console_for_session(session_id)
            if client_log_bytes and pii_scrub_requested:
                client_log_bytes = _scrub_gzip_bytes(client_log_bytes)
            if client_log_bytes:
                optional_files.append(
                    (
                        "client/console.log.gz",
                        client_log_bytes,
                        "text/plain+gzip",
                        "Client console events",
                    )
                )

        if include_ws_requested:
            include_meta["ws"] = bool(ws_bytes)
        if include_logs_requested:
            include_meta["logs"] = bool(client_log_bytes or server_log_entries)

        manifest_files = [
            _manifest_entry(
                "prompt.txt",
                prompt_bytes,
                content_type="text/plain; charset=utf-8",
                description="Flow analysis prompt",
            ),
            _manifest_entry(
                "events/flow.ndjson.gz",
                events_gz_bytes,
                content_type="application/x-ndjson+gzip",
                description="Session events (gzipped NDJSON)",
            ),
            _manifest_entry(
                "config/config.json",
                config_bytes,
                content_type="application/json",
                description="Session configuration snapshot",
            ),
            _manifest_entry(
                "server/server.log.gz",
                server_log_bytes,
                content_type="text/plain+gzip",
                description="Server admin log slice",
            ),
        ]

        if timeline_bytes:
            manifest_files.append(
                _manifest_entry(
                    "events/flow.json",
                    timeline_bytes,
                    content_type="application/json",
                    description="Key event timeline",
                )
            )

        for path, data, content_type, description in optional_files:
            manifest_files.append(
                _manifest_entry(
                    path,
                    data,
                    content_type=content_type,
                    description=description,
                )
            )

        manifest_meta: Dict[str, Any] = {"mode": mode, "redacted": False}
        if include_meta:
            manifest_meta["include"] = include_meta
        if privacy_meta:
            manifest_meta["privacy"] = privacy_meta
        if limits_meta:
            manifest_meta["limits"] = limits_meta
        if snapshot.started_at_iso:
            manifest_meta["session_started_at"] = snapshot.started_at_iso

        manifest_payload = {
            "schema_version": "1.0",
            "exported_at": generated_at,
            "session_id": session_id,
            "levels": levels,
            "event_count": event_count,
            "files": manifest_files,
        }
        if _HANDOFF_TELEMETRY_SCHEMAS:
            manifest_payload["telemetry_schemas"] = list(_HANDOFF_TELEMETRY_SCHEMAS)
        if manifest_meta:
            manifest_payload["meta"] = manifest_meta
        manifest_payload["policy_snapshot"] = policy_snapshot or {}

        manifest_bytes = json.dumps(
            manifest_payload, ensure_ascii=False, indent=2
        ).encode("utf-8")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("prompt.txt", prompt_bytes)
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("events/flow.ndjson.gz", events_gz_bytes)
            archive.writestr("config/config.json", config_bytes)
            archive.writestr("server/server.log.gz", server_log_bytes)
            if timeline_bytes:
                archive.writestr("events/flow.json", timeline_bytes)
            for path, data, _, _ in optional_files:
                archive.writestr(path, data)
        zip_bytes = buffer.getvalue()

    if max_bytes is not None and len(zip_bytes) > max_bytes:
        return _too_large_response()

    response = Response(zip_bytes, mimetype="application/zip")
    safe_session = re.sub(r"[^a-zA-Z0-9_-]+", "_", session_id) or "session"
    response.headers[
        "Content-Disposition"
    ] = f'attachment; filename="flow_handoff_{safe_session}.zip"'
    response.headers["X-Flow-Redacted"] = "0" if is_full else "1"
    response.headers["X-Flow-Payload-Bytes"] = str(len(zip_bytes))
    response.headers["X-Flow-Payload-Sha1"] = hashlib.sha1(zip_bytes).hexdigest()
    response.headers["X-Flow-Mode"] = mode
    if pii_scrub_requested:
        response.headers["X-Flow-PII-Scrubbed"] = "1"
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["bp"]
