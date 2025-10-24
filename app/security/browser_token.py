"""Helpers for extracting browser-provided tokens."""
from __future__ import annotations

from typing import Dict, Tuple
from urllib.parse import parse_qsl

Scope = Dict[str, object]


def extract_bearer_from_query(scope: Scope | None) -> Tuple[str | None, str | None]:
    """Extract a bearer token from the ASGI scope query string.

    Returns a ``(token, error)`` tuple. Errors are descriptive strings while a
    missing token is signaled via ``token is None`` and ``error is None``. The
    helper never raises.
    """

    if not scope:
        return None, None

    raw_query = scope.get("query_string", b"")
    if isinstance(raw_query, bytes):
        query_string = raw_query.decode("utf-8", "ignore")
    elif isinstance(raw_query, str):
        query_string = raw_query
    else:
        return None, "invalid query string"

    if not query_string:
        return None, None

    for key, value in parse_qsl(query_string, keep_blank_values=True):
        if key != "access_token":
            continue
        if value:
            return value, None
        return None, "empty access_token"

    return None, None


__all__ = ["extract_bearer_from_query"]
