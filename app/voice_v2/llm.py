"""Stubbed LLM adapter that emits EVT_NLG telemetry."""
from __future__ import annotations

import time
from typing import Any, Dict

from app.telemetry import bus
from app.voice_v2 import EVT_NLG


class LLMAdapter:
    """Return canned responses while publishing telemetry events."""

    def __init__(self, *, telemetry_bus=bus, canned_text: str | None = None) -> None:
        self._bus = telemetry_bus
        self._canned_text = canned_text or (
            "Thanks for chatting with AskChip! How else can I help?"
        )

    def generate(self, req_id: str, text: str, **_: Any) -> Dict[str, Any]:
        """Return a canned response and emit an EVT_NLG event."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        start = time.perf_counter()
        response_text = self._canned_text
        self._publish_nlg(req_id, response_text)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        timing = {"total_ms": max(elapsed_ms, 0)}
        return {"text": response_text, "timing": timing}

    def _publish_nlg(self, req_id: str, text: str) -> None:
        event = {
            "type": EVT_NLG,
            "req_id": req_id,
            "text": text,
            "source": "llm_adapter",
        }
        self._bus.publish(event)


__all__ = ["LLMAdapter"]
