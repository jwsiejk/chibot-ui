"""Time utility helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import time


def now_iso_utc() -> str:
    """Return the current UTC time in ISO-8601 format with 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_monotonic_ms() -> int:
    """Return the current monotonic clock in milliseconds as an integer."""
    return int(time.monotonic() * 1000)
