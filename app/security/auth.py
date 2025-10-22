"""Authentication helpers for WebSocket adapters."""
from __future__ import annotations

from typing import Dict, Tuple


def authorize(headers: Dict[str, str]) -> Tuple[bool, str | None]:
    """Simple bearer-token authorization gate."""

    auth_header = headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, "missing or invalid auth"
    return True, None


__all__ = ["authorize"]
