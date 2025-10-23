import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_FINAL, EVT_NLU
from app.voice_v2.engine import EngineV2


class TestNLUOncePerTurn(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)
        self.engine = EngineV2(telemetry_bus=bus)
        self.sid = "sid-nlu"
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=0)

    def tearDown(self) -> None:
        bus.unsubscribe(self.token)
        bus.reset()

    def _events(self, event_type: str) -> list[dict]:
        return [evt for evt in self.events if evt["type"] == event_type]

    def test_single_final_emits_nlu_once(self) -> None:
        self.engine.on_asr_final(self.sid, "hello there order 12345")

        finals = self._events(EVT_ASR_FINAL)
        nlus = self._events(EVT_NLU)
        self.assertEqual(1, len(finals))
        self.assertEqual(1, len(nlus))

        final_event = finals[0]
        nlu_event = nlus[0]
        self.assertEqual(final_event["req_id"], nlu_event["req_id"])
        self.assertEqual("1", nlu_event["schema_version"])
        self.assertIn("intent", nlu_event)
        self.assertIn("entities", nlu_event)
        self.assertIn("confidence", nlu_event)

        final_index = self.events.index(final_event)
        nlu_index = self.events.index(nlu_event)
        self.assertLess(final_index, nlu_index)

    def test_duplicate_finals_deduped(self) -> None:
        self.engine.on_asr_final(self.sid, "status update please")
        self.engine.on_asr_final(self.sid, "status update please")

        finals = self._events(EVT_ASR_FINAL)
        nlus = self._events(EVT_NLU)
        self.assertEqual(1, len(finals))
        self.assertEqual(1, len(nlus))

    def test_sessions_do_not_cross(self) -> None:
        sid_two = "sid-nlu-2"
        self.engine.on_open(sid_two, {})
        self.engine.on_audio(sid_two, b"def", seq=0)

        self.engine.on_asr_final(self.sid, "hello order 456")
        self.engine.on_asr_final(sid_two, "need help with product zenith")

        nlus = self._events(EVT_NLU)
        self.assertEqual(2, len(nlus))

        sid_map = {evt["sid"]: evt for evt in nlus}
        self.assertIn(self.sid, sid_map)
        self.assertIn(sid_two, sid_map)
        self.assertEqual(self.sid, sid_map[self.sid]["sid"])
        self.assertEqual(sid_two, sid_map[sid_two]["sid"])


if __name__ == "__main__":
    unittest.main()
