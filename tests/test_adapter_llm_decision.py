import asyncio
import unittest
import os

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.voice_v2 import (
    EVT_LLM_RESPONSE_END,
    EVT_LLM_RESPONSE_START,
    EVT_NLG,
    EVT_WS_JSON_SEND,
)
from app.ws.adapter import AdapterContext, ChatV2Adapter
from app.ws.state import mark


class _RecordingEngine:
    def __init__(self) -> None:
        self.asr_finals: list[tuple[str, str, str | None]] = []
        self.opens: list[tuple[str, dict]] = []

    async def on_open(self, sid: str, headers: dict) -> None:  # pragma: no cover - helper
        self.opens.append((sid, headers))

    async def start_greet(self, sid: str) -> None:  # pragma: no cover - helper
        bus.publish({"type": EVT_LLM_RESPONSE_START, "sid": sid, "purpose": "greet"})
        bus.publish({"type": EVT_LLM_RESPONSE_END, "sid": sid, "purpose": "greet"})
        bus.publish({"type": EVT_NLG, "sid": sid, "text": "hi there"})
        bus.publish(
            {
                "type": EVT_WS_JSON_SEND,
                "sid": sid,
                "frame": {"type": "chat.message", "role": "assistant", "text": "hi there"},
            }
        )

    def on_asr_final(self, sid: str, text: str, req_id: str | None = None) -> None:  # pragma: no cover - helper
        self.asr_finals.append((sid, text, req_id))
        bus.publish({"type": EVT_LLM_RESPONSE_START, "sid": sid, "req_id": req_id, "purpose": "chat"})
        bus.publish({"type": EVT_LLM_RESPONSE_END, "sid": sid, "req_id": req_id, "purpose": "chat"})
        bus.publish({"type": EVT_NLG, "sid": sid, "req_id": req_id, "text": f"echo:{text}"})


class TestAdapterLLMDecisions(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)
        self.engine = _RecordingEngine()

    def tearDown(self) -> None:
        if self.token:
            bus.unsubscribe(self.token)
        bus.reset()

    def _filtered_types(self, interesting: set[str]) -> list[str]:
        return [evt.get("type") for evt in self.events if evt.get("type") in interesting]

    def _attach_decision_logger(
        self, adapter: ChatV2Adapter, decisions: list[dict]
    ) -> None:
        original_log_event = adapter._log_event

        def _capture(self, level: str, evt: str, sid: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
            if evt == "EVT_LLM_TURN_DECISION":
                decisions.append(kwargs)
            original_log_event(level, evt, sid, **kwargs)

        adapter._log_event = _capture.__get__(adapter, ChatV2Adapter)  # type: ignore[assignment]

    def test_greet_pipeline_still_emits_llm_and_nlg(self) -> None:
        async def _run() -> None:
            adapter = ChatV2Adapter(engine=self.engine)
            ctx = AdapterContext(sid="sid-greet-decisions", headers={})
            await adapter._on_open_and_greet(ctx)

        asyncio.run(_run())

        interesting = {EVT_LLM_RESPONSE_START, EVT_LLM_RESPONSE_END, EVT_NLG, EVT_WS_JSON_SEND}
        types = self._filtered_types(interesting)

        self.assertIn(EVT_LLM_RESPONSE_START, types)
        self.assertIn(EVT_LLM_RESPONSE_END, types)
        self.assertIn(EVT_NLG, types)
        self.assertIn(EVT_WS_JSON_SEND, types)

    def test_timeout_final_skips_llm_turn(self) -> None:
        decisions: list[dict] = []

        async def _run() -> None:
            adapter = ChatV2Adapter(engine=self.engine)
            ctx = AdapterContext(sid="sid-timeout", headers={})
            ctx.asr_open = True
            mark(ctx.session, "open")
            ctx.asr_stream_id = "stream-timeout"
            ctx.turn_req_id = "req-timeout"
            ctx.active_req_id = "req-timeout"

            self._attach_decision_logger(adapter, decisions)

            await adapter._handle_asr_result(ctx, "", True, promoted_final=True, timeout=True)

        asyncio.run(_run())

        llm_starts = [evt for evt in self.events if evt.get("type") == EVT_LLM_RESPONSE_START]
        self.assertEqual([], llm_starts)

        decision_reasons = [d.get("reason") for d in decisions]
        self.assertIn("timeout_no_text", decision_reasons)

    def test_user_final_triggers_single_llm_and_nlg(self) -> None:
        decisions: list[dict] = []

        async def _run() -> None:
            adapter = ChatV2Adapter(engine=self.engine)
            ctx = AdapterContext(sid="sid-user-final", headers={})
            ctx.asr_open = True
            mark(ctx.session, "open")
            ctx.asr_stream_id = "stream-user"
            ctx.turn_req_id = "req-user"
            ctx.active_req_id = "req-user"

            self._attach_decision_logger(adapter, decisions)

            await adapter._handle_asr_result(ctx, "help with flashlight", True)

        asyncio.run(_run())

        llm_starts = [evt for evt in self.events if evt.get("type") == EVT_LLM_RESPONSE_START]
        llm_ends = [evt for evt in self.events if evt.get("type") == EVT_LLM_RESPONSE_END]
        nlgs = [evt for evt in self.events if evt.get("type") == EVT_NLG]

        self.assertEqual(1, len(llm_starts))
        self.assertEqual(1, len(llm_ends))
        self.assertEqual(1, len(nlgs))
        self.assertEqual(1, len(self.engine.asr_finals))

        decision_reasons = [d.get("reason") for d in decisions if d.get("decision") == "llm_turn"]
        self.assertIn("non_empty_user_final", decision_reasons)


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
