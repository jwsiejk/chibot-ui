"""Helpers for publishing session step telemetry events."""

from __future__ import annotations

from typing import Any, MutableMapping

from app.telemetry import bus as telemetry_bus


def emit_session_step(
    payload: MutableMapping[str, Any], *, telemetry_bus=telemetry_bus
) -> None:
    """Publish a session-step telemetry event ensuring schema metadata."""

    if "schema_version" not in payload:
        payload["schema_version"] = "1"
    telemetry_bus.publish(payload)

