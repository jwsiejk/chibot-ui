import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_TURN_ROLLUP, EVT_TURN_BEGIN
from app.voice_v2.engine import EngineV2


class TurnRollupPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)
        self.engine = EngineV2(telemetry_bus=bus)
        self.sid = "sid-rollup"
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"seed", seq=0)
        self.engine.policy_snapshot = {
            "barge_in_enabled": True,
            "auto_commit_when_ready": True,
        }

    def tearDown(self) -> None:
        try:
            self.engine.on_close(self.sid, 1000, "test_teardown")
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        if self.token is not None:
            bus.unsubscribe(self.token)
        bus.reset()

    def _find_events(self, event_type: str) -> list[dict]:
        return [evt for evt in self.events if evt.get("type") == event_type]

    def test_rollup_emitted_once_after_say_end(self) -> None:
        self.engine.on_asr_partial(self.sid, "", 0.8, "hello there")
        self.engine.on_asr_final(self.sid, "hello there")

        utt_id = "utt-001"
        self.engine.on_tts_start(self.sid, utt_id, post_hold_ms=0)
        # Emit 6,400 bytes of PCM audio => 200 ms of speech at 16 kHz mono.
        self.engine.emit_tts_audio_chunk(self.sid, b"\x00\x00" * 3200)
        canceled = self.engine.cancel_current_tts(self.sid, reason="ended")
        self.assertTrue(canceled)

        rollups = self._find_events(EVT_TURN_ROLLUP)
        self.assertEqual(len(rollups), 1)
        rollup = rollups[0]

        self.assertEqual(rollup.get("type"), EVT_TURN_ROLLUP)
        self.assertEqual(rollup.get("sid"), self.sid)
        begin_events = self._find_events(EVT_TURN_BEGIN)
        expected_req_id = None
        if begin_events:
            expected_req_id = begin_events[-1].get("req_id")
        self.assertIsInstance(rollup.get("req_id"), str)
        if expected_req_id:
            self.assertEqual(rollup.get("req_id"), expected_req_id)
        self.assertFalse(rollup.get("interruption_during_tts"))
        self.assertEqual(rollup.get("speech_ms"), 200)
        self.assertIn("asr_ms", rollup)
        self.assertIn("llm_ms", rollup)
        self.assertIn("tts_ms", rollup)
        self.assertIn("cost_est", rollup)
        self.assertIn("input_tokens", rollup)
        self.assertIn("output_tokens", rollup)

        if begin_events:
            expected_turn_id = (
                begin_events[-1].get("turn_id")
                or begin_events[-1].get("meta", {}).get("turn_id")
            )
            if expected_turn_id:
                self.assertEqual(rollup.get("turn_id"), expected_turn_id)

        # Ensure a second cancellation does not emit a duplicate rollup.
        self.engine.cancel_current_tts(self.sid, reason="ended")
        self.assertEqual(len(self._find_events(EVT_TURN_ROLLUP)), 1)


if __name__ == "__main__":
    unittest.main()
