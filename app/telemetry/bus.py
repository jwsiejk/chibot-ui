"""In-process telemetry bus with publish/subscribe support."""
from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, Tuple

Subscription = Tuple[str, Callable[[dict], None]]

# Internal registries keyed by subscription token.
_subscriptions: Dict[str, Subscription] = {}
# Index mapping event type to tokens for quick lookup.
_event_index: Dict[str, set[str]] = {}


def _now_ms() -> int:
    """Return the current time in milliseconds."""
    return int(time.time() * 1000)


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

    if "ts_ms" not in normalized or not isinstance(normalized["ts_ms"], int):
        normalized["ts_ms"] = _now_ms()
    if "level" not in normalized or not isinstance(normalized["level"], str):
        normalized["level"] = "debug"

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
