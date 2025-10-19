from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, Iterator, List, Optional

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
        return ("flow", "transition")
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


DEFAULT_HANDOFF_PROMPT = (
    "Analyze the redacted conversational flow; identify root cause(s), evidence (event IDs), "
    "smallest viable fix, and validation steps."
)

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

    store = FlowStore()

    def _generate() -> Iterable[str]:
        for event in _iter_session_events(store, session_id, levels, since_value):
            payload = _redact_event(event) if redacted else event
            yield json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"

    response = Response(stream_with_context(_generate()), mimetype="application/x-ndjson")
    response.headers["X-Flow-Redacted"] = "1" if redacted else "0"
    return response


@bp.post("/flow/handoff")
def flow_handoff():
    _require_admin()

    payload = request.get_json(silent=True) or {}
    session_id = _normalize_str(payload.get("session_id"))
    if not session_id:
        abort(400, description="session_id is required")

    levels = _coerce_levels(payload.get("levels"))
    prompt_value = payload.get("prompt")
    prompt_text = _normalize_str(prompt_value) or DEFAULT_HANDOFF_PROMPT

    store = FlowStore()
    events = list(_iter_session_events(store, session_id, levels, None))
    redacted_events = [_redact_event(event) for event in events]

    lines = [
        json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        for event in redacted_events
    ]
    ndjson_text = "\n".join(lines)
    if ndjson_text:
        ndjson_text += "\n"
    ndjson_bytes = ndjson_text.encode("utf-8")

    sha_hex = hashlib.sha1(ndjson_bytes).hexdigest()
    short_hash = sha_hex[:8]
    generated_at = datetime.now(timezone.utc).isoformat()

    meta_payload = {
        "session_id": session_id,
        "levels": levels,
        "generated_at": generated_at,
        "event_count": len(redacted_events),
        "payload_sig": {"bytes": len(ndjson_bytes), "sha1_8": short_hash},
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("flow.ndjson", ndjson_bytes)
        archive.writestr("prompt.txt", prompt_text)
        archive.writestr(
            "meta.json",
            json.dumps(meta_payload, ensure_ascii=False, indent=2),
        )
    buffer.seek(0)

    response = Response(buffer.getvalue(), mimetype="application/zip")
    safe_session = re.sub(r"[^a-zA-Z0-9_-]+", "_", session_id) or "session"
    response.headers[
        "Content-Disposition"
    ] = f'attachment; filename="flow_handoff_{safe_session}.zip"'
    response.headers["X-Flow-Redacted"] = "1"
    response.headers["X-Flow-Payload-Bytes"] = str(len(ndjson_bytes))
    response.headers["X-Flow-Payload-Sha1"] = short_hash
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["bp"]
