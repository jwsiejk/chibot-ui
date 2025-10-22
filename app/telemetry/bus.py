"""In-process telemetry bus with publish/subscribe support."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable, Dict, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

Subscription = Tuple[str, Callable[[dict], None]]

# Internal registries keyed by subscription token.
_subscriptions: Dict[str, Subscription] = {}
# Index mapping event type to tokens for quick lookup.
_event_index: Dict[str, set[str]] = {}


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
        except Exception as exc:  # pylint: disable=broad-except
            print(f"WARN telemetry redaction failed: {exc}")
            normalized["meta"] = original_meta

    tokens = set()
    tokens.update(_event_index.get(event_type, set()))
    tokens.update(_event_index.get("*", set()))

    for token in list(tokens):
        subscription = _subscriptions.get(token)
        if not subscription:
            continue
        _, handler = subscription
        try:
            handler(dict(normalized))
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Telemetry handler error for token {token}: {exc}")


def reset() -> None:
    """Clear all subscriptions (primarily for testing)."""
    _subscriptions.clear()
    _event_index.clear()
