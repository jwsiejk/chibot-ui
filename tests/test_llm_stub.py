import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_NLG
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

        other_types = [evt["type"] for evt in events if evt["type"] != EVT_NLG]
        self.assertEqual(other_types, [])
