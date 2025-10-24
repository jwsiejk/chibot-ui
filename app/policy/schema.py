"""Policy snapshot schemas for runtime consumers."""
from __future__ import annotations

from typing import Literal, Mapping, TypedDict, cast

AuthPolicyMode = Literal["required", "disabled"]


class AuthPolicySnapshot(TypedDict):
    """Minimal snapshot describing the active WebSocket auth policy."""

    ws_auth_mode: AuthPolicyMode


DEFAULT_WS_AUTH_MODE: AuthPolicyMode = "disabled"
DEFAULT_AUTH_POLICY: AuthPolicySnapshot = {"ws_auth_mode": DEFAULT_WS_AUTH_MODE}

_VALID_AUTH_MODES = {"required", "disabled"}


def coerce_ws_auth_mode(value: object, default: AuthPolicyMode = DEFAULT_WS_AUTH_MODE) -> AuthPolicyMode:
    """Return a normalized auth mode with validation."""

    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_AUTH_MODES:
            return cast(AuthPolicyMode, candidate)
    return default


def build_auth_policy_snapshot(
    payload: Mapping[str, object] | None,
    *,
    default: AuthPolicySnapshot | None = None,
) -> AuthPolicySnapshot:
    """Normalize a mapping into an :class:`AuthPolicySnapshot`."""

    base = dict(default or DEFAULT_AUTH_POLICY)
    if payload is None:
        return base

    raw_mode = payload.get("ws_auth_mode") if isinstance(payload, Mapping) else None
    return {"ws_auth_mode": coerce_ws_auth_mode(raw_mode, base["ws_auth_mode"])}


__all__ = [
    "AuthPolicyMode",
    "AuthPolicySnapshot",
    "DEFAULT_AUTH_POLICY",
    "DEFAULT_WS_AUTH_MODE",
    "build_auth_policy_snapshot",
    "coerce_ws_auth_mode",
]
