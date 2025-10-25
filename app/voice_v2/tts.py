"""Stubbed TTS adapter that emits EVT_TTS_START/END telemetry events."""
from __future__ import annotations

from typing import Any, Dict

from app.telemetry import bus
from app.voice_v2 import EVT_TTS_END, EVT_TTS_START


class TTSAdapter:
    """Minimal text-to-speech stub that publishes telemetry breadcrumbs."""

    def __init__(self, *, telemetry_bus=bus, post_hold_ms: int = 200) -> None:
        if not isinstance(post_hold_ms, (int, float)):
            raise TypeError("post_hold_ms must be numeric")
        post_hold_int = int(post_hold_ms)
        if post_hold_int < 0:
            raise ValueError("post_hold_ms must be >= 0")
        self._bus = telemetry_bus
        self._post_hold_ms = post_hold_int
        self._utt_seq = 0

    @property
    def post_hold_ms(self) -> int:
        """Return the configured post-hold duration."""

        return self._post_hold_ms

    def speak(self, req_id: str, text: str, **_: Any) -> Dict[str, Any]:
        """Emit start/end telemetry and return a synthetic response envelope."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        utt_id = self._next_utt_id()
        self._publish_start(req_id, utt_id, text)
        self._publish_end(req_id, utt_id)

        return {
            "utt_id": utt_id,
            "text": text,
            "post_hold_ms": self._post_hold_ms,
        }

    def stop(self) -> None:
        """Reset sequencing state so the next session starts fresh."""

        self._utt_seq = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _next_utt_id(self) -> str:
        self._utt_seq += 1
        return f"utt-{self._utt_seq:05d}"

    def _publish_start(self, req_id: str, utt_id: str, text: str) -> None:
        event = {
            "type": EVT_TTS_START,
            "req_id": req_id,
            "utt_id": utt_id,
            "text": text,
            "post_hold_ms": self._post_hold_ms,
            "source": "tts_adapter",
        }
        self._publish(event)

    def _publish_end(self, req_id: str, utt_id: str) -> None:
        event = {
            "type": EVT_TTS_END,
            "req_id": req_id,
            "utt_id": utt_id,
            "source": "tts_adapter",
        }
        self._publish(event)

    def _publish(self, event: dict) -> None:
        self._bus.publish(event)


__all__ = ["TTSAdapter"]
