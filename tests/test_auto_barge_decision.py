import unittest
import time

from app.voice_v2 import (
    EVT_BARGE_CONFIRMED,
    EVT_BARGE_DETECTED,
    EVT_BARGE_REJECTED,
)
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
        barge_types = [
            e["type"]
            for e in self.events
            if e["type"] in {EVT_BARGE_DETECTED, EVT_BARGE_CONFIRMED, EVT_BARGE_REJECTED}
        ]
        self.assertEqual(barge_types, [EVT_BARGE_DETECTED, EVT_BARGE_CONFIRMED])

        confirmed = next(
            e for e in self.events if e["type"] == EVT_BARGE_CONFIRMED
        )
        self.assertTrue(confirmed["meta"]["barge"]["granted"])

    def test_denied_barge_no_transition(self) -> None:
        self.engine.policy_snapshot = {"barge_in_enabled": False}
        sid = "s2"
        self.engine.on_tts_start(sid, "u2")
        self.engine.on_auto_barge_attempt(sid, "auto_vad")
        barge_events = [
            e
            for e in self.events
            if e["type"] in {EVT_BARGE_DETECTED, EVT_BARGE_CONFIRMED, EVT_BARGE_REJECTED}
        ]
        self.assertEqual(len(barge_events), 2)
        detected, rejected = barge_events
        self.assertEqual(detected["type"], EVT_BARGE_DETECTED)
        self.assertFalse(detected["meta"]["barge"]["granted"])
        self.assertEqual(detected["meta"]["barge"].get("reason"), "policy_disabled")
        self.assertEqual(rejected["type"], EVT_BARGE_REJECTED)
        self.assertFalse(rejected["meta"]["barge"]["granted"])
        self.assertEqual(rejected["meta"]["barge"].get("reason"), "policy_disabled")


if __name__ == "__main__":
    unittest.main()
