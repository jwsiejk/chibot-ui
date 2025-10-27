import unittest

from app.voice_v2.engine import (
    EngineV2,
    EVT_TURN_BEGIN,
    EVT_TURN_END,
)


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:  # pragma: no cover - exercised in tests
        self.events.append(dict(event))


class _NullExporter:
    def write(self, sid: str, event: dict) -> None:  # pragma: no cover - stub
        return


class TurnCorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _FakeBus()
        self.engine = EngineV2(fake_exporter=_NullExporter(), telemetry_bus=self.bus)
        self.sid = "sid-correlation"

    def _events(self, event_type: str) -> list[dict]:
        return [evt for evt in self.bus.events if evt["type"] == event_type]

    def test_turn_context_tracks_req_and_turn(self) -> None:
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=1)
        self.engine.on_asr_partial(self.sid, "req-1", 0.9, "hi")

        begin_events = self._events(EVT_TURN_BEGIN)
        self.assertEqual(1, len(begin_events))
        begin_event = begin_events[0]

        turn_id = begin_event["turn_id"]
        req_id = begin_event["req_id"]
        self.assertIsInstance(turn_id, str)
        self.assertTrue(turn_id)
        self.assertIsInstance(req_id, str)
        self.assertTrue(req_id)

        context = self.engine.turn_context(self.sid)
        self.assertEqual({"turn_id": turn_id, "req_id": req_id}, context)

        self.engine.on_asr_final(self.sid, "hi")
        self.assertEqual(context, self.engine.turn_context(self.sid))

        self.engine.on_tts_start(self.sid, "utt-1")
        self.assertEqual(context, self.engine.turn_context(self.sid))

        self.engine.on_tts_end(self.sid, "utt-1")

        end_events = self._events(EVT_TURN_END)
        self.assertEqual(1, len(end_events))
        end_event = end_events[0]
        self.assertEqual(turn_id, end_event["turn_id"])
        self.assertEqual(req_id, end_event["req_id"])

        self.assertIsNone(self.engine.turn_context(self.sid))

    def test_req_id_changes_between_turns(self) -> None:
        self.engine.on_open(self.sid, {})

        # First turn
        self.engine.on_audio(self.sid, b"abc", seq=1)
        self.engine.on_asr_partial(self.sid, "req-1", 0.9, "hi")
        first_begin = self._events(EVT_TURN_BEGIN)[0]
        first_req_id = first_begin["req_id"]
        first_turn_id = first_begin["turn_id"]
        self.engine.on_asr_final(self.sid, "hi")
        self.engine.on_tts_start(self.sid, "utt-1")
        self.engine.on_tts_end(self.sid, "utt-1")
        first_end = self._events(EVT_TURN_END)[0]
        self.assertEqual(first_turn_id, first_end["turn_id"])
        self.assertEqual(first_req_id, first_end["req_id"])

        # Second turn
        self.engine.on_audio(self.sid, b"def", seq=1)
        self.engine.on_asr_partial(self.sid, "req-2", 0.9, "hello")
        second_begin = self._events(EVT_TURN_BEGIN)[-1]
        second_req_id = second_begin["req_id"]
        second_turn_id = second_begin["turn_id"]

        self.assertNotEqual(first_req_id, second_req_id)
        self.assertNotEqual(first_turn_id, second_turn_id)


if __name__ == "__main__":
    unittest.main()
