import asyncio
import unittest

from app.telemetry import bus
from app.voice_v2 import (
    EVT_LLM_RESPONSE_END,
    EVT_LLM_RESPONSE_START,
    EVT_NLG,
    EVT_WS_JSON_SEND,
)
from app.voice_v2.engine import EngineV2, LISTENING
from app.ws.adapter import AdapterContext, ChatV2Adapter
from app.ws.state import mark


class _RecordingPolicyDecider:
    def __init__(self) -> None:
        self.decisions: list[dict] = []

    def decide(self, req_id: str, nlu: dict | None, snapshot: dict | None) -> dict:  # pragma: no cover - simple stub
        payload = {"action": "respond", "rule": "chat.user_turn"}
        if isinstance(nlu, dict):
            payload.update({"intent": nlu.get("intent"), "nlu_text": nlu.get("text")})
        self.decisions.append(payload)
        return {"action": "respond", "rule": "chat.user_turn", "actions": [], "barge_in_enabled": False, "auto_commit_when_ready": False}


class _RecordingNLU:
    def extract(self, req_id: str, text: str) -> dict:  # pragma: no cover - simple stub
        return {"intent": "chat.user_turn", "entities": {}, "text": text}


class _RecordingLLM:
    def __init__(self) -> None:
        self._provider = type("_Provider", (), {"default_model": "stub-model"})()
        self.generated: list[dict] = []
        self._turn_lookup: dict[str, tuple[str, str]] = {}
        self._bus_token = bus.subscribe("*", self._handle_turn_event)

    def _handle_turn_event(self, event: dict) -> None:  # pragma: no cover - subscription helper
        if event.get("type") != EVT_WS_JSON_SEND:
            return

        frame = event.get("frame") or event.get("payload", {}).get("frame")
        if not isinstance(frame, dict):
            return

        frame_type = frame.get("type")
        if frame_type == "turn.begin":
            sid = event.get("sid")
            req_id = frame.get("req_id")
            turn_id = frame.get("turn_id")
            if all(isinstance(val, str) and val for val in (sid, req_id, turn_id)):
                self._turn_lookup[req_id] = (sid, turn_id)
        elif frame_type == "turn.end":
            req_id = frame.get("req_id")
            if isinstance(req_id, str) and req_id:
                self._turn_lookup.pop(req_id, None)

    def generate(self, req_id: str, **kwargs) -> dict:  # pragma: no cover - deterministic stub
        self.generated.append({"req_id": req_id, **kwargs})
        return {"text": "stubbed llm response"}

    def generate_persona(self, *args, **kwargs):  # pragma: no cover - compatibility shim
        return "stubbed llm response"

    def publish_chat_message(self, req_id: str, text: str) -> None:  # pragma: no cover - deterministic stub
        mapping = self._turn_lookup.get(req_id)
        if not mapping:
            return
        sid, turn_id = mapping
        frame = {
            "type": "chat.message",
            "id": "assistant-msg",
            "role": "assistant",
            "text": text,
            "origin": "voice",
            "turn_id": turn_id,
            "req_id": req_id,
        }
        bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "frame": frame})


class _RecordingEngine(EngineV2):
    def __init__(self) -> None:
        super().__init__(telemetry_bus=bus)
        self._policy_decider = _RecordingPolicyDecider()
        self._nlu = _RecordingNLU()
        self._llm = _RecordingLLM()


class TurnPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)

    def tearDown(self) -> None:
        if self.token:
            bus.unsubscribe(self.token)
        bus.reset()

    def _attach_decision_logger(self, adapter: ChatV2Adapter, decisions: list[dict]) -> None:
        original_log_event = adapter._log_event

        def _capture(self, level: str, evt: str, sid: str, **kwargs):  # type: ignore[no-untyped-def]
            if evt == "EVT_LLM_TURN_DECISION":
                decisions.append(kwargs)
            original_log_event(level, evt, sid, **kwargs)

        adapter._log_event = _capture.__get__(adapter, ChatV2Adapter)  # type: ignore[assignment]

    def test_full_user_final_triggers_llm_and_nlg_pipeline(self) -> None:
        decisions: list[dict] = []

        async def _run() -> None:
            engine = _RecordingEngine()
            adapter = ChatV2Adapter(engine=engine)
            ctx = AdapterContext(sid="sid-full-turn", headers={})
            ctx.asr_open = True
            mark(ctx.session, "open")
            ctx.asr_stream_id = "stream-full"
            ctx.turn_req_id = "req-full"
            ctx.active_req_id = "req-full"

            session = engine._ensure_session(ctx.sid)
            session.state = LISTENING
            session.turn_id = "turn-full"
            session.req_id = ctx.turn_req_id

            self._attach_decision_logger(adapter, decisions)

            await adapter._handle_asr_result(
                ctx, "I need help with FlashArray", True, promoted_final=False, timeout=False
            )

        asyncio.run(_run())

        llm_starts = [evt for evt in self.events if evt.get("type") == EVT_LLM_RESPONSE_START]
        llm_ends = [evt for evt in self.events if evt.get("type") == EVT_LLM_RESPONSE_END]
        nlgs = [evt for evt in self.events if evt.get("type") == EVT_NLG]
        ws_frames = [evt.get("frame") or evt.get("payload", {}).get("frame") for evt in self.events if evt.get("type") == EVT_WS_JSON_SEND]

        self.assertEqual(1, len(llm_starts))
        self.assertEqual(1, len(llm_ends))
        self.assertEqual(1, len(nlgs))

        decision_payloads = [d for d in decisions if d.get("decision") == "llm_turn"]
        self.assertEqual(1, len(decision_payloads))
        self.assertEqual("non_empty_user_final", decision_payloads[0].get("reason"))

        assistant_frames = [frame for frame in ws_frames if isinstance(frame, dict) and frame.get("role") == "assistant" and frame.get("type") == "chat.message"]
        self.assertGreaterEqual(len(assistant_frames), 1)


if __name__ == "__main__":  # pragma: no cover - direct execution helper
    unittest.main()
