import unittest

from app.voice_v2 import EVT_ASR_FINAL
from app.voice_v2.engine import EngineV2, EVT_TURN_BEGIN, EVT_TURN_END


class _FakeExporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, sid: str, event: dict) -> None:  # pragma: no cover - exercised in tests
        self.events.append((sid, dict(event)))


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:  # pragma: no cover - exercised in tests
        self.events.append(dict(event))


class TurnStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _FakeBus()
        self.exporter = _FakeExporter()
        self.engine = EngineV2(self.exporter, telemetry_bus=self.bus)
        self.sid = "sid-turn"

    def _turn_events(self, event_type: str) -> list[dict]:
        return [evt for evt in self.bus.events if evt["type"] == event_type]

    def test_basic_turn_flow(self) -> None:
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=1)
        self.engine.on_asr_final(self.sid, "hi")
        self.engine.on_tts_start(self.sid, "utt-1")
        self.engine.on_tts_end(self.sid, "utt-1")

        begin_events = self._turn_events(EVT_TURN_BEGIN)
        end_events = self._turn_events(EVT_TURN_END)

        self.assertEqual(len(begin_events), 1)
        self.assertEqual(len(end_events), 1)

        begin_event = begin_events[0]
        end_event = end_events[0]

        self.assertIn("turn_id", begin_event["meta"])
        self.assertIn("turn_id", end_event["meta"])
        self.assertEqual(begin_event["meta"]["turn_id"], end_event["meta"]["turn_id"])

        self.assertGreater(end_event["meta"]["duration_ms"], 0)

        begin_index = self.bus.events.index(begin_event)
        end_index = self.bus.events.index(end_event)
        self.assertLess(begin_index, end_index)

    def test_new_turn_id_on_second_listening(self) -> None:
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=1)
        self.engine.on_asr_final(self.sid, "hi")
        self.engine.on_tts_start(self.sid, "utt-1")
        self.engine.on_tts_end(self.sid, "utt-1")

        first_turn_id = self._turn_events(EVT_TURN_BEGIN)[0]["meta"]["turn_id"]

        self.engine.on_audio(self.sid, b"def", seq=1)
        self.engine.on_asr_final(self.sid, "hello")
        self.engine.on_tts_start(self.sid, "utt-2")
        self.engine.on_tts_end(self.sid, "utt-2")

        begin_events = self._turn_events(EVT_TURN_BEGIN)
        self.assertEqual(len(begin_events), 2)
        second_turn_id = begin_events[-1]["meta"]["turn_id"]

        self.assertNotEqual(first_turn_id, second_turn_id)

    def test_stale_final_req_id_ignored(self) -> None:
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=1)

        first_begin = self._turn_events(EVT_TURN_BEGIN)[0]
        first_req_id = first_begin["req_id"]

        self.engine.on_asr_final(self.sid, "hi", req_id=first_req_id)
        self.engine.on_tts_start(self.sid, "utt-1")
        self.engine.on_tts_end(self.sid, "utt-1")

        first_final_count = len(self._turn_events(EVT_ASR_FINAL))

        self.engine.on_audio(self.sid, b"def", seq=2)
        current_req_id = self._turn_events(EVT_TURN_BEGIN)[-1]["req_id"]
        self.assertNotEqual(first_req_id, current_req_id)

        self.engine.on_asr_final(self.sid, "late final", req_id=first_req_id)

        finals_after_stale = len(self._turn_events(EVT_ASR_FINAL))
        self.assertEqual(first_final_count, finals_after_stale)


if __name__ == "__main__":
    unittest.main()
