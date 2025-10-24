"""Logging configuration helpers for the AskChip runtime."""

from __future__ import annotations

import logging
from typing import Iterable, Mapping, MutableSet

from app.policy.loader import InteractionPolicySnapshot, load_interaction_policy

_LEVEL_ALIASES = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}

_LOG_CONFIGURED = False
_MANAGED_LOGGERS: MutableSet[logging.Logger] = set()


def _coerce_level(value: object, fallback: int) -> int:
    """Translate a level name or number to a logging level constant."""

    if value is None:
        return fallback

    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    candidate = str(value).strip()
    if not candidate:
        return fallback

    upper = candidate.upper()
    if upper in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[upper]

    try:
        numeric = int(candidate)
    except ValueError:
        return fallback

    return numeric


def _set_level(logger: logging.Logger, level: int) -> None:
    """Apply the resolved level to a logger and its handlers."""

    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


def _apply_level(level: int) -> None:
    if not _MANAGED_LOGGERS:
        _MANAGED_LOGGERS.add(logging.getLogger())

    for logger in _MANAGED_LOGGERS:
        _set_level(logger, level)


def _extract_telemetry(policy: Mapping[str, object] | None) -> Mapping[str, object]:
    if not isinstance(policy, Mapping):
        return {}

    telemetry = policy.get("telemetry") if "telemetry" in policy else None
    if isinstance(telemetry, Mapping):
        return telemetry

    if any(key in policy for key in ("enabled", "level", "categories", "sampling", "redaction")):
        return policy

    return {}


def apply_logging_policy(policy: InteractionPolicySnapshot | Mapping[str, object] | None) -> None:
    """Adjust managed loggers according to the telemetry policy block."""

    telemetry = _extract_telemetry(policy)

    if not telemetry:
        return

    enabled = bool(telemetry.get("enabled", True))
    fallback_level = logging.WARNING if not enabled else logging.INFO
    desired_level = _coerce_level(telemetry.get("level") if enabled else telemetry.get("fallback_level"), fallback_level)
    _apply_level(desired_level)


def configure_logging(*, extra_loggers: Iterable[str] | None = None) -> None:
    """Ensure Python logging follows the interaction policy telemetry settings."""

    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    _MANAGED_LOGGERS.add(root)

    if extra_loggers is None:
        extra_loggers = ("uvicorn", "uvicorn.error", "uvicorn.access")

    for name in extra_loggers:
        logger = logging.getLogger(name)
        _MANAGED_LOGGERS.add(logger)

    policy = load_interaction_policy()
    apply_logging_policy(policy)

    _LOG_CONFIGURED = True


__all__ = ["apply_logging_policy", "configure_logging"]

