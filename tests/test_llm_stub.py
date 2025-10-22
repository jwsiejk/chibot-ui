import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_NLG, EVT_WS_JSON_SEND
from app.voice_v2.engine import EVT_TURN_BEGIN
from app.voice_v2.llm import LLMAdapter


class TestLLMStub(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_generate_emits_single_event(self) -> None:
        adapter = LLMAdapter(canned_text="Stub reply")
        events: list[dict] = []
        token = bus.subscribe("*", events.append)
        try:
            bus.publish(
                {
                    "type": EVT_TURN_BEGIN,
                    "sid": "sid-123",
                    "req_id": "req-123",
                    "turn_id": "turn-123",
                }
            )
            result = adapter.generate(req_id="req-123", text="Hello?")
        finally:
            bus.unsubscribe(token)

        self.assertEqual(result["text"], "Stub reply")
        self.assertIn("timing", result)
        self.assertIsInstance(result["timing"], dict)
        self.assertIn("total_ms", result["timing"])
        self.assertGreaterEqual(result["timing"]["total_ms"], 0)

        nlg_events = [evt for evt in events if evt["type"] == EVT_NLG]
        self.assertEqual(len(nlg_events), 1)
        nlg_event = nlg_events[0]
        self.assertEqual(nlg_event["req_id"], "req-123")
        self.assertEqual(nlg_event["text"], "Stub reply")

        chat_events = [evt for evt in events if evt["type"] == EVT_WS_JSON_SEND]
        self.assertEqual(len(chat_events), 1)
        chat_event = chat_events[0]
        self.assertEqual(chat_event["sid"], "sid-123")
        payload = chat_event["payload"]
        self.assertIsInstance(payload, dict)
        frame = payload.get("frame")
        self.assertIsInstance(frame, dict)
        self.assertEqual(frame["type"], "chat.message")
        self.assertEqual(frame["role"], "assistant")
        self.assertEqual(frame["origin"], "voice")
        self.assertEqual(frame["turn_id"], "turn-123")
        self.assertEqual(frame["req_id"], "req-123")
        self.assertEqual(frame["text"], "Stub reply")

        event_types = {evt["type"] for evt in events}
        self.assertEqual(event_types, {EVT_TURN_BEGIN, EVT_NLG, EVT_WS_JSON_SEND})
