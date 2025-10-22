"""Mic gate controller for reasoning mask state."""
from __future__ import annotations

from typing import Callable, Dict


class GateController:
    """Tracks reasoned mic mask state."""

    _SUPPORTED_REASONS = ("tts_active", "manual_gate", "system_hold")

    def __init__(self, publish: Callable[[dict], None]) -> None:
        """Create a gate controller with a telemetry publisher."""
        self._publish = publish
        self._reasons: Dict[str, bool] = {name: False for name in self._SUPPORTED_REASONS}

    def set_reason(
        self,
        reason: str,
        on: bool,
        *,
        sid: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Set a reason mask value and publish on change."""
        if reason not in self._reasons:
            return

        current = self._reasons[reason]
        if current == on:
            return

        self._reasons[reason] = on
        self._publish_event(changed_reason=reason, sid=sid, extra_meta=meta)

    def clear_all(self, *, sid: str | None = None) -> None:
        """Clear all reasons and publish if the gate opens."""
        if not any(self._reasons.values()):
            return

        changed = False
        for reason in self._reasons:
            if self._reasons[reason]:
                self._reasons[reason] = False
                changed = True

        if changed:
            self._publish_event(changed_reason="clear_all", sid=sid)

    def snapshot(self) -> dict:
        """Return a snapshot of reasons and effective state."""
        reasons = {name: self._reasons[name] for name in self._SUPPORTED_REASONS}
        return {"reasons": reasons, "effective": any(reasons.values())}

    # Internal helpers -------------------------------------------------

    def _publish_event(
        self,
        *,
        changed_reason: str,
        sid: str | None,
        extra_meta: dict | None = None,
    ) -> None:
        reasons = {name: self._reasons[name] for name in self._SUPPORTED_REASONS}
        effective = any(reasons.values())
        mask = effective
        state = "on" if mask else "off"
        reason_label = self._resolve_reason(reasons, default=changed_reason)

        gate_meta = {
            "state": state,
            "mask": mask,
            "reason": reason_label,
            "reasons": reasons,
        }

        if extra_meta:
            gate_meta.update(extra_meta)

        envelope = {
            "type": "EVT_MIC_GATE",
            "level": "debug",
            "meta": {"gate": gate_meta},
        }
        if sid is not None:
            envelope["sid"] = sid

        self._publish(envelope)

    def _resolve_reason(self, reasons: Dict[str, bool], default: str) -> str:
        active = [name for name, value in reasons.items() if value]
        if not active:
            return default if default != "clear_all" else "none"
        if len(active) > 1:
            return "multi"
        return active[0]

