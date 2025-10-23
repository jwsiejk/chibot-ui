"""Stubbed LLM adapter used by the voice engine."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Mapping

from app.telemetry import bus
from app.voice_v2 import EVT_NLG, EVT_WS_JSON_SEND
from app.voice_v2 import generator
from app.voice_v2.persona import load_persona

EVT_TURN_BEGIN = "EVT_TURN_BEGIN"
EVT_TURN_END = "EVT_TURN_END"


class LLMAdapter:
    """Return canned responses while optionally publishing telemetry events."""

    _INTENT_REPLIES = {
        "greeting": "Hi there! How can I help you today?",
        "goodbye": "Thanks for stopping by. Talk soon!",
        "status.check": "Let me pull up the latest status for you.",
        "support.request": "I'm here to help. Could you share a few more details?",
    }

    def __init__(
        self,
        *,
        telemetry_bus=bus,
        canned_text: str | None = None,
        auto_publish: bool = True,
    ) -> None:
        self._bus = telemetry_bus
        self._auto_publish = bool(auto_publish)
        self._canned_text = canned_text or (
            "Thanks for chatting with AskChip! How else can I help?"
        )
        self._turn_lookup: dict[str, tuple[str, str]] = {}
        subscribe = getattr(self._bus, "subscribe", None)
        if callable(subscribe):
            self._subscriptions = [
                subscribe(EVT_TURN_BEGIN, self._handle_turn_event),
                subscribe(EVT_TURN_END, self._handle_turn_event),
            ]
        else:
            self._subscriptions = []

    def generate(
        self,
        req_id: str,
        intent: str | None = None,
        entities: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """Return a canned response for the provided ``intent``.

        ``entities`` are currently unused but accepted so future call sites can
        pass structured data without needing a signature change. The adapter can
        still be called with ``text="..."`` to preserve compatibility with
        legacy callers and tests.
        """

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        _ = kwargs.get("text")  # legacy callers provide the user utterance via ``text``
        response_text = self._INTENT_REPLIES.get(intent or "", self._canned_text)

        start = time.perf_counter()
        timing = {"total_ms": max(int((time.perf_counter() - start) * 1000), 0)}
        if self._auto_publish:
            self.publish_nlg(req_id, response_text)
        return {"text": response_text, "timing": timing}

    def generate_persona(
        self,
        sid: str,
        turn_id: str,
        req_id: str,
        user_text: str,
        plan: Mapping[str, Any] | object,
    ) -> str:
        """Return a persona-aligned reply using the rule-based generator."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        persona = load_persona()
        messages = generator.build_messages(persona, plan, user_text)
        reply = generator.render_reply(messages)
        if not isinstance(reply, str):
            reply = str(reply)
        self.publish_nlg(req_id, reply)
        return reply

    def publish_nlg(self, req_id: str, text: str) -> None:
        """Publish EVT_NLG and chat bridge messages when enabled."""

        if not self._auto_publish:
            # When auto publish is disabled we only emit the bus events when
            # this helper is invoked directly (the engine handles it).
            return

        self._publish_nlg(req_id, text)
        self._publish_chat_message(req_id, text)

    def publish_chat_message(self, req_id: str, text: str) -> None:
        """Manually mirror the assistant message into the chat stream."""

        self._publish_chat_message(req_id, text)

    def _publish_nlg(self, req_id: str, text: str) -> None:
        event = {"type": EVT_NLG, "req_id": req_id, "text": text, "source": "llm_adapter"}
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

    def _handle_turn_event(self, event: Mapping[str, Any]) -> None:
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
