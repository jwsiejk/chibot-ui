import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_FINAL, EVT_NLU, EVT_NLG
from app.voice_v2.engine import EngineV2
from app.voice_v2.policy_decider import EVT_POLICY_DECISION


class TestPolicyAndNLGOncePerTurn(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.events: list[dict] = []
        self.token = bus.subscribe("*", self.events.append)
        self.engine = EngineV2(telemetry_bus=bus)
        self.sid = "sid-policy"
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"abc", seq=0)

    def tearDown(self) -> None:
        bus.unsubscribe(self.token)
        bus.reset()

    def _events(self, event_type: str, *, sid: str | None = None) -> list[dict]:
        events = [evt for evt in self.events if evt["type"] == event_type]
        if sid is None:
            return events
        return [evt for evt in events if evt.get("sid") == sid]

    def test_happy_path_emits_policy_and_nlg_once(self) -> None:
        self.engine.policy_snapshot = {
            "barge_in_enabled": True,
            "auto_commit_when_ready": True,
        }

        self.engine.on_asr_final(self.sid, "hello there")

        finals = self._events(EVT_ASR_FINAL, sid=self.sid)
        nlus = self._events(EVT_NLU, sid=self.sid)
        policies = self._events(EVT_POLICY_DECISION, sid=self.sid)
        nlgs = self._events(EVT_NLG, sid=self.sid)

        self.assertEqual(1, len(finals))
        self.assertEqual(1, len(nlus))
        self.assertEqual(1, len(policies))
        self.assertEqual(1, len(nlgs))

        final_event = finals[0]
        nlu_event = nlus[0]
        policy_event = policies[0]
        nlg_event = nlgs[0]

        req_id = final_event["req_id"]
        self.assertEqual(req_id, nlu_event["req_id"])
        self.assertEqual(req_id, policy_event["req_id"])
        self.assertEqual(req_id, nlg_event["req_id"])

        self.assertEqual("respond", policy_event["action"])
        self.assertTrue(policy_event["barge_in_enabled"])
        self.assertTrue(policy_event["auto_commit_when_ready"])
        self.assertEqual("Hi there! How can I help you today?", nlg_event["text"])

        self.assertEqual("1", policy_event["schema_version"])
        self.assertEqual("1", nlg_event["schema_version"])

        final_index = self.events.index(final_event)
        nlu_index = self.events.index(nlu_event)
        policy_index = self.events.index(policy_event)
        nlg_index = self.events.index(nlg_event)

        self.assertLess(final_index, nlu_index)
        self.assertLess(nlu_index, policy_index)
        self.assertLess(policy_index, nlg_index)

    def test_duplicate_finals_deduped(self) -> None:
        self.engine.policy_snapshot = {
            "barge_in_enabled": False,
            "auto_commit_when_ready": False,
        }

        self.engine.on_asr_final(self.sid, "status update please")
        self.engine.on_asr_final(self.sid, "status update please")

        policies = self._events(EVT_POLICY_DECISION, sid=self.sid)
        nlgs = self._events(EVT_NLG, sid=self.sid)

        self.assertEqual(1, len(policies))
        self.assertEqual(1, len(nlgs))

    def test_sessions_do_not_cross(self) -> None:
        sid_two = "sid-policy-2"
        self.engine.on_open(sid_two, {})
        self.engine.on_audio(sid_two, b"xyz", seq=0)

        self.engine.policy_snapshot = {
            "barge_in_enabled": True,
            "auto_commit_when_ready": False,
        }

        self.engine.on_asr_final(self.sid, "need help with order 123")
        self.engine.on_asr_final(sid_two, "hello agent")

        policies = self._events(EVT_POLICY_DECISION)
        nlgs = self._events(EVT_NLG)

        self.assertEqual(2, len(policies))
        self.assertEqual(2, len(nlgs))

        sid_to_policy = {evt["sid"]: evt for evt in policies}
        sid_to_nlg = {evt["sid"]: evt for evt in nlgs}

        self.assertIn(self.sid, sid_to_policy)
        self.assertIn(sid_two, sid_to_policy)
        self.assertIn(self.sid, sid_to_nlg)
        self.assertIn(sid_two, sid_to_nlg)

        for sid, evt in sid_to_policy.items():
            self.assertEqual(sid, evt["sid"])
            self.assertEqual("respond", evt["action"])
            self.assertEqual("1", evt["schema_version"])

        for sid, evt in sid_to_nlg.items():
            self.assertEqual(sid, evt["sid"])
            self.assertEqual("1", evt["schema_version"])


if __name__ == "__main__":
    unittest.main()
