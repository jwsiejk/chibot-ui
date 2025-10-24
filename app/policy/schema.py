"""Policy snapshot schemas for runtime consumers."""
from __future__ import annotations

from typing import Literal, Mapping, TypedDict, cast

AuthPolicyMode = Literal["required", "disabled"]
LoggingLevel = Literal["debug", "info", "warn", "error"]


class AuthPolicySnapshot(TypedDict):
    """Minimal snapshot describing the active WebSocket auth policy."""

    ws_auth_mode: AuthPolicyMode
    logging_level: LoggingLevel


DEFAULT_WS_AUTH_MODE: AuthPolicyMode = "required"
DEFAULT_LOGGING_LEVEL: LoggingLevel = "debug"
DEFAULT_AUTH_POLICY: AuthPolicySnapshot = {
    "ws_auth_mode": DEFAULT_WS_AUTH_MODE,
    "logging_level": DEFAULT_LOGGING_LEVEL,
}

_VALID_AUTH_MODES = {"required", "disabled"}
_VALID_LOGGING_LEVELS = {"debug", "info", "warn", "error"}


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
    raw_logging = payload.get("logging_level") if isinstance(payload, Mapping) else None
    return {
        "ws_auth_mode": coerce_ws_auth_mode(raw_mode, base["ws_auth_mode"]),
        "logging_level": coerce_logging_level(raw_logging, base["logging_level"]),
    }


def coerce_logging_level(
    value: object, default: LoggingLevel = DEFAULT_LOGGING_LEVEL
) -> LoggingLevel:
    """Return a normalized logging level with validation."""

    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _VALID_LOGGING_LEVELS:
            return cast(LoggingLevel, candidate)
    return default


__all__ = [
    "AuthPolicyMode",
    "AuthPolicySnapshot",
    "DEFAULT_WS_AUTH_MODE",
    "DEFAULT_LOGGING_LEVEL",
    "DEFAULT_AUTH_POLICY",
    "build_auth_policy_snapshot",
    "coerce_ws_auth_mode",
    "coerce_logging_level",
    "LoggingLevel",
]
