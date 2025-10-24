"""Simplified authentication helpers that allow all requests."""
from __future__ import annotations

from typing import Dict, Tuple

_ANONYMOUS_PRINCIPAL: Dict[str, object] = {"sub": "anonymous"}


def verify_jwt(token: str) -> Tuple[bool, str | None, Dict[str, object] | None]:
    """Return a failure tuple indicating JWT validation is disabled."""

    return False, "token authentication disabled", None


def authorize(
    headers: Dict[str, str] | None,
    *,
    allow_query_token: bool | None = None,
    scope: Dict[str, object] | None = None,
    require_token: bool | None = None,
):
    """Authorize a request without enforcing bearer tokens."""

    return True, None, dict(_ANONYMOUS_PRINCIPAL)


def authorize_admin(
    headers: Dict[str, str] | None,
    scope: Dict[str, object],
    *,
    require_token: bool | None = None,
) -> Tuple[bool, str | None, Dict[str, object] | None]:
    """Authorize admin HTTP requests without token requirements."""

    return True, None, dict(_ANONYMOUS_PRINCIPAL)


__all__ = ["authorize", "authorize_admin", "verify_jwt"]
