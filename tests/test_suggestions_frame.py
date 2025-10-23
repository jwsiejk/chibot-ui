from __future__ import annotations

import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_WS_JSON_SEND
from app.voice_v2.engine import EngineV2


class TestAssistantSuggestionsFrame(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)
        self.engine = EngineV2(telemetry_bus=bus)
        self.sid = "sid-suggestions"
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"pcm", seq=0)

    def tearDown(self) -> None:
        bus.unsubscribe(self.token)
        bus.reset()

    def _ws_frames(self, frame_type: str) -> list[dict]:
        return [
            evt
            for evt in self.events
            if evt["type"] == EVT_WS_JSON_SEND
            and isinstance(evt.get("frame"), dict)
            and evt["frame"].get("type") == frame_type
        ]

    def test_emits_single_suggestions_frame(self) -> None:
        utterance = "can you walk me through the install steps"
        self.engine.on_asr_final(self.sid, utterance)

        suggestion_frames = self._ws_frames("assistant.suggestions")
        plan_frames = self._ws_frames("dialog.plan")

        self.assertEqual(1, len(plan_frames))
        self.assertEqual(1, len(suggestion_frames))

        plan_index = self.events.index(plan_frames[0])
        suggestions_index = self.events.index(suggestion_frames[0])
        self.assertLess(plan_index, suggestions_index)

        items = suggestion_frames[0]["frame"].get("items")
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 1)
        self.assertLessEqual(len(items), 3)
        for item in items:
            self.assertIsInstance(item, dict)
            self.assertEqual("action", item.get("kind"))
            label = item.get("label")
            self.assertIsInstance(label, str)
            self.assertTrue(label)

        self.assertEqual("steps", suggestion_frames[0]["frame"].get("mode"))

        # A duplicate final should not emit a second suggestions frame.
        self.engine.on_asr_final(self.sid, utterance)
        self.assertEqual(1, len(self._ws_frames("assistant.suggestions")))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
