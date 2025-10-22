"""Stubbed LLM adapter that emits EVT_NLG telemetry."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Tuple

from app.telemetry import bus
from app.voice_v2 import EVT_NLG, EVT_WS_JSON_SEND
from app.voice_v2.engine import EVT_TURN_BEGIN, EVT_TURN_END


class LLMAdapter:
    """Return canned responses while publishing telemetry events."""

    def __init__(self, *, telemetry_bus=bus, canned_text: str | None = None) -> None:
        self._bus = telemetry_bus
        self._canned_text = canned_text or (
            "Thanks for chatting with AskChip! How else can I help?"
        )
        self._turn_lookup: dict[str, Tuple[str, str]] = {}
        self._subscriptions = [
            self._bus.subscribe(EVT_TURN_BEGIN, self._handle_turn_event),
            self._bus.subscribe(EVT_TURN_END, self._handle_turn_event),
        ]

    def generate(self, req_id: str, text: str, **_: Any) -> Dict[str, Any]:
        """Return a canned response and emit an EVT_NLG event."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        start = time.perf_counter()
        response_text = self._canned_text
        self._publish_nlg(req_id, response_text)
        self._publish_chat_message(req_id, response_text)
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

    def _publish_chat_message(self, req_id: str, text: str) -> None:
        mapping = self._turn_lookup.get(req_id)
        if not mapping:
            return

        sid, turn_id = mapping
        if not sid or not turn_id:
            return

        frame = {
            "type": "chat.message",
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "text": text,
            "origin": "voice",
            "turn_id": turn_id,
            "req_id": req_id,
            "ts_ms": int(time.time() * 1000),
        }

        serialized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "meta": {
                "ws": {
                    "dir": "out",
                    "size": len(serialized.encode("utf-8")),
                    "preview": serialized,
                }
            },
            "frame": frame,
        }

        event = {"type": EVT_WS_JSON_SEND, "sid": sid, "payload": payload}
        self._bus.publish(event)

    def _handle_turn_event(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == EVT_TURN_BEGIN:
            sid = event.get("sid")
            req_id = event.get("req_id")
            turn_id = event.get("turn_id")
            if (
                isinstance(sid, str)
                and sid
                and isinstance(req_id, str)
                and req_id
                and isinstance(turn_id, str)
                and turn_id
            ):
                self._turn_lookup[req_id] = (sid, turn_id)
        elif event_type == EVT_TURN_END:
            req_id = event.get("req_id")
            if isinstance(req_id, str) and req_id:
                self._turn_lookup.pop(req_id, None)


__all__ = ["LLMAdapter"]
