import unittest
import time

from app.voice_v2.engine import EngineV2


class TestAutoBargeDecision(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = EngineV2(fake_exporter=None)
        self.events: list[dict] = []
        self.engine._publish = lambda e: self.events.append(e)

    def test_granted_barge_transitions(self) -> None:
        self.engine.policy_snapshot = {"barge_in_enabled": True}
        sid = "s1"
        self.engine.on_tts_start(sid, "u1")
        self.engine.on_auto_barge_attempt(sid, "auto_vad")
        time.sleep(0.5)  # allow confirming→listening
        phases = [
            e["meta"]["barge"]["phase"]
            for e in self.events
            if e["type"] == "EVT_BARGE_IN"
        ]
        self.assertEqual(phases, ["detected", "confirmed"])
        confirmed = [
            e for e in self.events if e["type"] == "EVT_BARGE_IN" and e["meta"]["barge"]["phase"] == "confirmed"
        ][0]
        self.assertTrue(confirmed["meta"]["barge"]["granted"])

    def test_denied_barge_no_transition(self) -> None:
        self.engine.policy_snapshot = {"barge_in_enabled": False}
        sid = "s2"
        self.engine.on_tts_start(sid, "u2")
        self.engine.on_auto_barge_attempt(sid, "auto_vad")
        phases = [
            e for e in self.events if e["type"] == "EVT_BARGE_IN"
        ]
        self.assertEqual(len(phases), 2)
        detected, rejected = phases
        self.assertEqual(detected["meta"]["barge"]["phase"], "detected")
        self.assertEqual(rejected["meta"]["barge"]["phase"], "rejected")
        self.assertFalse(rejected["meta"]["barge"]["granted"])
        self.assertEqual(rejected["meta"]["barge"].get("reason"), "policy_disabled")


if __name__ == "__main__":
    unittest.main()
