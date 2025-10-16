from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List

__all__ = ["record_turn_metrics", "get_recent"]

_MAX_HISTORY = 200
_buffer: Deque[Dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
_lock = threading.Lock()


def _sanitize_metrics(metrics: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in metrics.items():
        if value is None:
            continue
        sanitized[key] = value
    return sanitized


def record_turn_metrics(turn_id: Any, metrics: Dict[str, Any] | None) -> Dict[str, Any]:
    """Store metrics for a completed ASR turn in a bounded ring buffer."""

    entry: Dict[str, Any] = {
        "turn_id": turn_id,
        "recorded_at_ms": int(time.time() * 1000),
    }
    entry.update(_sanitize_metrics(metrics))
    with _lock:
        _buffer.append(entry)
    return dict(entry)


def get_recent(n: int = 10) -> List[Dict[str, Any]]:
    """Return the most recent ``n`` ASR turn metrics (newest first)."""

    if n <= 0:
        return []
    with _lock:
        items = list(_buffer)[-n:]
    items.reverse()
    return [dict(item) for item in items]
