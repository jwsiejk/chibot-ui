"""In-process telemetry bus with publish/subscribe support."""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Callable, Dict, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

Subscription = Tuple[str, Callable[[dict], None]]

_bus_log = logging.getLogger("app.telemetry.bus")

# Internal registries keyed by subscription token.
_subscriptions: Dict[str, Subscription] = {}
# Index mapping event type to tokens for quick lookup.
_event_index: Dict[str, set[str]] = {}

# Track per-session TTS metadata for sanitised audio telemetry.
_tts_current_utt: Dict[str, str] = {}
_tts_audio_totals: Dict[str, int] = {}


def _now_ms() -> int:
    """Return the current time in milliseconds."""
    return int(time.time() * 1000)


_TOKEN_KEYWORDS = {"token", "key", "sig", "secret", "auth"}
_AUTH_KEYWORDS = {"authorization", "auth"}
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-+/=]+)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{32,64}$")


def _mask_tail(value: str, keep: int = 4) -> str:
    """Return a masked token preserving only the last characters."""
    if keep <= 0:
        return "****"
    if len(value) <= keep:
        return "****"
    return "****" + value[-keep:]


def _shorten_opaque(value: str) -> str:
    """Collapse long opaque secrets to a short representation."""
    if len(value) < 6:
        return value
    return f"{value[:3]}…{value[-3:]}"


def _shorten_generic(value: str) -> str:
    """Collapse overly long strings while preserving edges."""
    return f"{value[:8]}…{value[-8:]}"


def _redact_string(raw: str, key_hint: str | None = None) -> str:
    """Apply best-effort redaction rules to a string value."""
    value = raw

    # Mask sensitive query parameters in URLs.
    try:
        split = urlsplit(value)
        query = split.query
        source_value = value
        if not query and "?" not in value and "=" in value and " " not in value:
            # Treat bare query strings (e.g., "token=abc") as the query payload.
            query = value
            source_value = ""
        if query:
            pairs = parse_qsl(query, keep_blank_values=True)
            masked = False
            new_pairs = []
            for k, v in pairs:
                if k.lower() in _TOKEN_KEYWORDS:
                    masked = True
                    new_pairs.append((k, "****"))
                else:
                    new_pairs.append((k, v))
            if masked:
                redacted_query = urlencode(new_pairs, doseq=True)
                if source_value:
                    value = urlunsplit(
                        (
                            split.scheme,
                            split.netloc,
                            split.path,
                            redacted_query,
                            split.fragment,
                        )
                    )
                else:
                    value = redacted_query
    except Exception:
        # Ignore URL parsing failures; fall back to other rules.
        pass

    # Authorization/Bearer token masking.
    if _BEARER_RE.search(value):
        def _replace(match: re.Match[str]) -> str:
            token = match.group(2)
            return match.group(1) + _mask_tail(token)

        value = _BEARER_RE.sub(_replace, value)
    elif key_hint and key_hint.lower() in _AUTH_KEYWORDS:
        stripped = value.strip()
        masked = _mask_tail(stripped)
        prefix_len = len(value) - len(value.lstrip())
        suffix_len = len(value) - len(value.rstrip())
        value = value[:prefix_len] + masked + value[len(value) - suffix_len :]

    # Email masking.
    value = _EMAIL_RE.sub(lambda match: f"***@{match.group(2)}", value)

    # Opaque secret masking.
    if _OPAQUE_RE.match(value):
        value = _shorten_opaque(value)

    # Generic long string collapse.
    if len(value) > 128:
        value = _shorten_generic(value)

    return value


def _redact_meta(meta: Any, key_hint: str | None = None) -> Any:
    """Return a deep-redacted copy of the provided meta payload."""
    if isinstance(meta, dict):
        return {key: _redact_meta(value, key) for key, value in meta.items()}
    if isinstance(meta, list):
        return [_redact_meta(item) for item in meta]
    if isinstance(meta, tuple):
        return tuple(_redact_meta(item) for item in meta)
    if isinstance(meta, str):
        return _redact_string(meta, key_hint)
    return meta


def redact_payload(payload: Any) -> Any:
    """Return a deep-redacted copy of an arbitrary telemetry payload."""

    return _redact_meta(payload)


def _clone_payload(payload: Any) -> Any:
    """Return a deep copy suitable for dispatch to subscribers."""

    if isinstance(payload, dict):
        return {key: _clone_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_clone_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(_clone_payload(item) for item in payload)
    return payload


def _json_safe(obj: Any) -> Any:
    """Return a JSON-safe representation of the provided object."""

    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"__type": "bytes", "len": len(obj)}
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(item) for item in obj]
    return obj


def _extract_audio_chunk_len(event: Dict[str, Any]) -> int | None:
    """Best-effort extraction of the chunk length from an audio event."""

    meta = event.get("meta")
    if isinstance(meta, dict):
        byte_count = meta.get("byte_count")
        if isinstance(byte_count, (int, float)):
            size = int(byte_count)
            return max(0, size)

    chunk = event.get("chunk")
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        return len(chunk)
    return None


def _increment_audio_total(sid: str, byte_count: int) -> int:
    """Update and return the running audio total for the session."""

    if byte_count < 0:
        byte_count = 0
    total = _tts_audio_totals.get(sid, 0) + byte_count
    _tts_audio_totals[sid] = total
    return total


def note_tts_start(sid: str, utt_id: str) -> None:
    """Register the active TTS utterance for a session."""

    if not isinstance(sid, str) or not sid:
        return
    if not isinstance(utt_id, str) or not utt_id:
        return
    _tts_current_utt[sid] = utt_id
    _tts_audio_totals[sid] = 0


def note_tts_end(sid: str, utt_id: str | None = None) -> None:
    """Clear any tracked TTS metadata for the session."""

    if not isinstance(sid, str) or not sid:
        return
    _tts_audio_totals.pop(sid, None)
    if utt_id is None:
        _tts_current_utt.pop(sid, None)
        return
    current = _tts_current_utt.get(sid)
    if current == utt_id:
        _tts_current_utt.pop(sid, None)


def subscribe(event_type: str, handler: Callable[[dict], None]) -> str:
    """Register a handler for a specific event type or wildcard."""
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event_type must be a non-empty string")
    if not callable(handler):
        raise ValueError("handler must be callable")

    token = f"sub_{uuid.uuid4().hex}"
    _subscriptions[token] = (event_type, handler)
    _event_index.setdefault(event_type, set()).add(token)
    return token


def unsubscribe(token: str) -> bool:
    """Remove a previously registered subscription."""
    subscription = _subscriptions.pop(token, None)
    if not subscription:
        return False

    event_type, _ = subscription
    tokens = _event_index.get(event_type)
    if tokens is not None:
        tokens.discard(token)
        if not tokens:
            _event_index.pop(event_type, None)
    return True


def publish(event: dict) -> None:
    """Normalize and dispatch an event to registered handlers."""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")

    normalized = dict(event)
    event_type = normalized.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("event['type'] must be a non-empty string")

    if "schema_version" not in normalized:
        normalized["schema_version"] = "1"

    if "ts_ms" not in normalized or not isinstance(normalized["ts_ms"], int):
        normalized["ts_ms"] = _now_ms()
    if "level" not in normalized or not isinstance(normalized["level"], str):
        normalized["level"] = "debug"

    if "meta" in normalized:
        original_meta = normalized["meta"]
        try:
            normalized["meta"] = _redact_meta(original_meta)
        except Exception:  # pylint: disable=broad-except
            _bus_log.warning("evt=telemetry_redact_failed", exc_info=True)
            normalized["meta"] = original_meta

    sid = normalized.get("sid")
    sid_str = sid if isinstance(sid, str) else None

    if event_type == "EVT_TTS_START" and sid_str:
        utt_meta = normalized.get("meta") or {}
        if isinstance(utt_meta, dict):
            tts_meta = utt_meta.get("tts")
            if isinstance(tts_meta, dict):
                utt_id = tts_meta.get("utt_id")
                if isinstance(utt_id, str) and utt_id:
                    note_tts_start(sid_str, utt_id)

    safe_event = _json_safe(normalized)
    if isinstance(safe_event, dict):
        if event_type == "EVT_WS_AUDIO_SEND":
            chunk_len = _extract_audio_chunk_len(normalized)
            total_bytes = None
            if sid_str and chunk_len is not None:
                total_bytes = _increment_audio_total(sid_str, chunk_len)
                utt_id = _tts_current_utt.get(sid_str)
                if utt_id:
                    safe_event.setdefault("utt_id", utt_id)
            safe_event.pop("chunk", None)
            if chunk_len is not None:
                safe_event["bytes"] = chunk_len
            if total_bytes is not None:
                safe_event["total_bytes"] = total_bytes
        elif event_type == "EVT_TTS_END" and sid_str:
            total = _tts_audio_totals.get(sid_str)
            if total is None and sid_str in _tts_current_utt:
                total = 0
            if total is not None:
                safe_event["total_bytes"] = total
            utt_id = _tts_current_utt.get(sid_str)
            if utt_id:
                safe_event.setdefault("utt_id", utt_id)

    tokens = set()
    tokens.update(_event_index.get(event_type, set()))
    tokens.update(_event_index.get("*", set()))

    for token in list(tokens):
        subscription = _subscriptions.get(token)
        if not subscription:
            continue
        _, handler = subscription
        try:
            if getattr(handler, "_telemetry_accepts_binary", False):
                payload = _clone_payload(normalized)
            else:
                payload = _clone_payload(safe_event)
            handler(payload)
        except Exception:  # pylint: disable=broad-except
            handler_name = getattr(handler, "__qualname__", repr(handler))
            _bus_log.error(
                "evt=telemetry_sink_failed token=%s handler=%s",
                token,
                handler_name,
                exc_info=True,
            )

    if event_type == "EVT_TTS_END" and sid_str:
        note_tts_end(sid_str, _tts_current_utt.get(sid_str))


def reset() -> None:
    """Clear all subscriptions (primarily for testing)."""
    _subscriptions.clear()
    _event_index.clear()
    _tts_current_utt.clear()
    _tts_audio_totals.clear()
