"""In-memory admin log journal used by diagnostics and tests.

The log is intentionally lightweight: it keeps a bounded history of recent
events, normalises common fields, and returns copies so callers cannot mutate
internal state.  The queue is safe to use from multiple threads in the WSGI
process.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from time import time
from typing import Any, Iterable, Mapping

HistoryItem = dict[str, Any]

_HISTORY: deque[HistoryItem] = deque(maxlen=1000)
_STEP = 0
_LOCK = Lock()

__all__ = [
    "admin_log_emit",
    "clear_admin_log_history_for_tests",
    "get_admin_log_history",
    "emit",
]


def _now_ms() -> int:
    return int(time() * 1000)


def _clip_preview(value: Any, *, limit: int = 160) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"


def _normalize_session_id(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:  # pragma: no cover - extremely defensive
        return None
    return text or None


def _normalize_turn_id(value: Any) -> str | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:  # pragma: no cover - extremely defensive
        return None
    return text or None


def _coerce_event(payload: Mapping[str, Any]) -> HistoryItem:
    data: HistoryItem = dict(payload)

    event_name = data.get("event") or data.get("kind") or data.get("label")
    if not isinstance(event_name, str) or not event_name.strip():
        event_name = "log"
    event_name = event_name.strip()
    data["event"] = event_name
    data.setdefault("kind", event_name)

    version = data.get("v")
    try:
        data["v"] = int(version)
    except Exception:
        data["v"] = 1

    ts_ms = data.get("ts_ms") or data.get("sent_at") or data.get("ts")
    try:
        ts_ms_int = int(ts_ms)
    except Exception:
        ts_ms_int = _now_ms()
    data["ts_ms"] = ts_ms_int
    data.setdefault("ts", ts_ms_int / 1000.0)

    sid = _normalize_session_id(data.pop("session_id", None) or data.get("sid"))
    if sid is not None:
        data["session_id"] = sid
        data["sid"] = sid
    else:
        data.pop("sid", None)

    turn_id = (
        data.pop("turnId", None)
        or data.pop("turn", None)
        or data.get("turn_id")
    )
    norm_turn = _normalize_turn_id(turn_id)
    if norm_turn is not None:
        data["turn_id"] = norm_turn
    else:
        data.pop("turn_id", None)

    if "text_preview" in data:
        data["text_preview"] = _clip_preview(data["text_preview"])

    if "text" in data:
        text_val = data["text"]
        if isinstance(text_val, str):
            data["text"] = text_val[:4096]

    payload_section = data.get("payload")
    if isinstance(payload_section, Mapping):
        data["payload"] = {k: payload_section[k] for k in payload_section}

    return data


def admin_log_emit(event: Mapping[str, Any] | None) -> HistoryItem | None:
    """Record an event and return the stored copy."""

    if not isinstance(event, Mapping):
        return None

    global _STEP

    with _LOCK:
        normalised = _coerce_event(event)
        _STEP += 1
        normalised["step"] = _STEP
        _HISTORY.append(dict(normalised))

    return dict(normalised)


def emit(kind: str, **fields: Any) -> HistoryItem | None:
    payload: dict[str, Any] = {"event": kind, "kind": kind, **fields}
    return admin_log_emit(payload)


def get_admin_log_history(*, limit: int | None = None, after_step: int | None = None) -> list[HistoryItem]:
    """Return a copy of the current history."""

    with _LOCK:
        items: Iterable[HistoryItem] = list(_HISTORY)

    if after_step is not None:
        items = [evt for evt in items if int(evt.get("step", 0)) > after_step]

    if limit is not None and limit >= 0:
        # materialise slice to avoid leaking deque internals
        items = list(items)[-limit:]
    else:
        items = list(items)

    return [dict(evt) for evt in items]


def clear_admin_log_history_for_tests() -> None:
    """Reset global state for unit tests."""

    global _STEP
    with _LOCK:
        _HISTORY.clear()
        _STEP = 0

