"""Stubbed LLM adapter used by the voice engine."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import time
import uuid
from typing import Any, Dict, Mapping

from app.services.llm.provider import create_from_env
from app.telemetry import bus
from app.voice_v2 import EVT_NLG, EVT_WS_JSON_SEND
from app.voice_v2 import generator
from app.voice_v2.llm_base import (
    LLMProviderBase,
    ProviderCircuitOpenError,
    ProviderTimeoutError,
)
from app.voice_v2.persona import (
    build_system_preamble,
    load_persona,
    maybe_pick_quote_for_sid,
)

EVT_TURN_BEGIN = "EVT_TURN_BEGIN"
EVT_TURN_END = "EVT_TURN_END"


_logger = logging.getLogger(__name__)


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
        provider: LLMProviderBase | None = None,
        canned_text: str | None = None,
        auto_publish: bool = True,
    ) -> None:
        self._bus = telemetry_bus
        self._auto_publish = bool(auto_publish)
        self._canned_text = canned_text or (
            "Thanks for chatting with AskChip! How else can I help?"
        )
        self._provider = provider or create_from_env(telemetry_bus=telemetry_bus)
        self._turn_lookup: dict[str, tuple[str, str]] = {}
        self._turn_counter_by_sid: Dict[str, int] = {}
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
        """Return a persona-aligned reply using either the provider or fallback."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        persona = load_persona()
        rule_based_messages = generator.build_messages(persona, plan, user_text)
        system_preamble = build_system_preamble(persona)

        # Prepend the explicit persona preamble for LLM providers while keeping the
        # remainder of the deterministic stack intact for fallback use.
        provider_messages: list[dict[str, str]] = [{"role": "system", "content": system_preamble}]
        skipped_system = False
        for message in rule_based_messages:
            if (
                not skipped_system
                and isinstance(message, Mapping)
                and message.get("role") == "system"
            ):
                skipped_system = True
                continue
            if isinstance(message, Mapping):
                provider_messages.append({
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                })

        reply: str | None = None
        fallback_reason: str | None = None
        provider = self._provider
        provider_ready = bool(provider and getattr(provider, "is_configured", False))
        if provider_ready:
            model_name = getattr(provider, "default_model", None) or "gpt-4o-mini"
            try:
                reply_candidate = self._invoke_provider(
                    provider,
                    provider_messages,
                    model=model_name,
                    temperature=0.6,
                )
            except ProviderCircuitOpenError:
                fallback_reason = "breaker_open"
            except ProviderTimeoutError:
                fallback_reason = "timeout"
            except Exception:  # pragma: no cover - defensive logging
                fallback_reason = "error"
                _logger.exception("LLM provider invocation failed; falling back to generator")
            else:
                if isinstance(reply_candidate, str) and reply_candidate.strip():
                    reply = reply_candidate
                else:
                    fallback_reason = "empty_response"

        if reply is None:
            reply = generator.render_reply(rule_based_messages)
            if not isinstance(reply, str):
                reply = str(reply)
        metadata: Dict[str, Any] | None = None
        if fallback_reason:
            metadata = {"source": "llm_fallback", "reason": fallback_reason}
        sid_key: str | None = sid if isinstance(sid, str) and sid else None
        if sid_key:
            turn_no = self._turn_counter_by_sid.get(sid_key, 0) + 1
            self._turn_counter_by_sid[sid_key] = turn_no
            mode = ""
            if isinstance(plan, Mapping):
                candidate_mode = plan.get("mode")
                if isinstance(candidate_mode, str):
                    mode = candidate_mode
            quote = maybe_pick_quote_for_sid(persona, sid_key, mode, turn_no)
            if quote:
                quote_text = quote.get("text")
                quote_id = quote.get("id")
                if isinstance(quote_text, str) and quote_text:
                    reply = f"{reply.rstrip()} (As my granddad would say: \"{quote_text}\")"
                    if isinstance(quote_id, str) and quote_id:
                        identifier = quote_id
                    else:
                        identifier = f"quote_turn_{turn_no}"
                    if metadata is None:
                        metadata = {}
                    metadata["quote_id"] = identifier
        self.publish_nlg(req_id, reply, metadata=metadata)
        return reply

    def generate_greeting(
        self,
        sid: str,
        turn_id: str,
        req_id: str,
        plan: Mapping[str, Any] | object | None = None,
    ) -> str:
        """Return a provider-crafted greeting constrained to the greet persona mode."""

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        _ = plan  # plan payload included for symmetry with persona generation
        _ = turn_id  # turn metadata is unused for greet but kept for parity
        _ = sid  # greet generation is session-aware but currently stateless

        persona = load_persona()
        system_preamble = build_system_preamble(persona)
        developer_instruction = self._greet_instruction(persona)
        messages = [
            {"role": "system", "content": system_preamble},
            {"role": "developer", "content": developer_instruction},
            {"role": "user", "content": ""},
        ]

        reply: str | None = None
        fallback_reason: str | None = None
        provider = self._provider
        provider_ready = bool(provider and getattr(provider, "is_configured", False))
        if provider_ready:
            model_name = getattr(provider, "default_model", None) or "gpt-4o-mini"
            try:
                reply_candidate = self._invoke_provider(
                    provider,
                    messages,
                    model=model_name,
                    temperature=0.4,
                    max_tokens=30,
                    purpose="greet",
                )
            except ProviderCircuitOpenError:
                fallback_reason = "breaker_open"
            except ProviderTimeoutError:
                fallback_reason = "timeout"
            except Exception:  # pragma: no cover - defensive logging
                fallback_reason = "error"
                _logger.exception(
                    "LLM provider invocation failed during greeting; falling back to static copy"
                )
            else:
                normalized = self._normalize_greet_reply(reply_candidate)
                if normalized:
                    reply = normalized
                else:
                    fallback_reason = "empty_response"

        if reply is None:
            reply = ""

        metadata: Dict[str, Any] | None = None
        if fallback_reason:
            metadata = {
                "source": "llm_fallback",
                "reason": fallback_reason,
                "purpose": "greet",
            }

        self.publish_nlg(req_id, reply, metadata=metadata)
        return reply

    def _invoke_provider(
        self,
        provider: LLMProviderBase,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        **extra_kwargs: Any,
    ) -> str:
        async def _execute() -> Any:
            return await provider.generate(
                messages=messages,
                model=model,
                temperature=temperature,
                **extra_kwargs,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_execute())
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _execute())
                result = future.result()

        return result if isinstance(result, str) else str(result)

    @staticmethod
    def _greet_instruction(persona: Mapping[str, Any] | object) -> str:
        if isinstance(persona, Mapping):
            modes = persona.get("modes")
            if isinstance(modes, Mapping):
                greet_mode = modes.get("greet")
                if isinstance(greet_mode, Mapping):
                    instruction = greet_mode.get("instruction")
                    if isinstance(instruction, str) and instruction.strip():
                        return instruction.strip()
        return "Generate a friendly Pure Storage greeting in under eight words."

    @staticmethod
    def _normalize_greet_reply(candidate: Any) -> str:
        if not isinstance(candidate, str):
            return ""
        normalized = " ".join(candidate.strip().split())
        if not normalized:
            return ""
        words = normalized.split()
        if len(words) > 8:
            normalized = " ".join(words[:8])
        return normalized

    def publish_nlg(
        self,
        req_id: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Publish EVT_NLG and chat bridge messages when enabled."""

        if not self._auto_publish:
            # When auto publish is disabled we only emit the bus events when
            # this helper is invoked directly (the engine handles it).
            return

        self._publish_nlg(req_id, text, metadata=metadata)
        self._publish_chat_message(req_id, text)

    def publish_chat_message(self, req_id: str, text: str) -> None:
        """Manually mirror the assistant message into the chat stream."""

        self._publish_chat_message(req_id, text)

    def _publish_nlg(
        self,
        req_id: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        event = {"type": EVT_NLG, "req_id": req_id, "text": text, "source": "llm_adapter"}
        if isinstance(metadata, Mapping):
            payload = {key: metadata[key] for key in metadata}
            if payload:
                event["metadata"] = payload
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
