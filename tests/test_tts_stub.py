import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_TTS_END, EVT_TTS_START
from app.voice_v2.tts import TTSAdapter


class TestTTSStub(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_speak_emits_start_then_end(self) -> None:
        adapter = TTSAdapter(post_hold_ms=150)
        events: list[dict] = []
        token = bus.subscribe("*", events.append)
        try:
            result = adapter.speak(req_id="req-42", text="Hello world")
        finally:
            bus.unsubscribe(token)

        self.assertIn("utt_id", result)
        utt_id = result["utt_id"]
        self.assertTrue(utt_id.startswith("utt-"))
        self.assertEqual(result["post_hold_ms"], 150)
        self.assertEqual(result["text"], "Hello world")

        start_events = [evt for evt in events if evt["type"] == EVT_TTS_START]
        end_events = [evt for evt in events if evt["type"] == EVT_TTS_END]

        self.assertEqual(len(start_events), 1)
        self.assertEqual(len(end_events), 1)

        start_event = start_events[0]
        end_event = end_events[0]

        self.assertEqual(start_event["utt_id"], utt_id)
        self.assertEqual(end_event["utt_id"], utt_id)
        self.assertEqual(start_event["post_hold_ms"], 150)
        self.assertEqual(start_event["req_id"], "req-42")
        self.assertEqual(end_event["req_id"], "req-42")

        start_index = events.index(start_event)
        end_index = events.index(end_event)
        self.assertLess(start_index, end_index)

        for evt in (start_event, end_event):
            self.assertEqual(evt["source"], "tts_adapter")


if __name__ == "__main__":
    unittest.main()
