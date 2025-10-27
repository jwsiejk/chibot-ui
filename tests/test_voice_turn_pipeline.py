import unittest

from app.telemetry import bus
from app.voice_v2 import (
    EVT_ASR_FINAL,
    EVT_NLU,
    EVT_NLG,
    EVT_TTS_END,
    EVT_TTS_START,
    EVT_LLM_RESPONSE_END,
    EVT_LLM_RESPONSE_START,
)
from app.voice_v2.engine import EngineV2
from app.voice_v2.policy_decider import EVT_POLICY_DECISION
from app.voice_v2.tts import TTSAdapter


class _RecordingBusProxy:
    """Proxy ``bus`` that records publish order while delegating behavior."""

    def __init__(self, inner_bus):
        self._inner = inner_bus
        self.published: list[dict] = []

    def publish(self, event: dict) -> None:
        snapshot = dict(event)
        self.published.append(snapshot)
        self._inner.publish(snapshot)

    def subscribe(self, event_type: str, handler):
        return self._inner.subscribe(event_type, handler)

    def unsubscribe(self, token: str) -> None:
        self._inner.unsubscribe(token)


class TestVoiceTurnPipeline(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        self.bus_proxy = _RecordingBusProxy(bus)
        self.events: list[dict] = []
        self.token_all = self.bus_proxy.subscribe("*", self.events.append)

        self.tts_adapter = TTSAdapter(telemetry_bus=self.bus_proxy)

        def _trigger_tts(event: dict) -> None:
            req_id = event.get("req_id")
            text = event.get("text")
            if isinstance(req_id, str) and isinstance(text, str) and text.strip():
                self.tts_adapter.speak(req_id=req_id, text=text)

        self.token_nlg = self.bus_proxy.subscribe(EVT_NLG, _trigger_tts)

        self.engine = EngineV2(telemetry_bus=self.bus_proxy)
        self.sid = "sid-turn"
        self.engine.on_open(self.sid, {})
        self.engine.on_audio(self.sid, b"seed", seq=0)

    def tearDown(self) -> None:
        if self.token_all is not None:
            self.bus_proxy.unsubscribe(self.token_all)
        if self.token_nlg is not None:
            self.bus_proxy.unsubscribe(self.token_nlg)
        bus.reset()

    def _published_by_type(self, event_type: str) -> list[dict]:
        return [evt for evt in self.bus_proxy.published if evt.get("type") == event_type]

    def test_first_turn_event_order(self) -> None:
        self.engine.policy_snapshot = {
            "barge_in_enabled": True,
            "auto_commit_when_ready": True,
        }

        self.engine.on_asr_final(self.sid, "hello there status update")

        interesting = {
            EVT_ASR_FINAL,
            EVT_NLU,
            EVT_POLICY_DECISION,
            EVT_LLM_RESPONSE_START,
            EVT_LLM_RESPONSE_END,
            EVT_NLG,
            EVT_TTS_START,
            EVT_TTS_END,
        }
        ordered_types = [
            evt["type"]
            for evt in self.bus_proxy.published
            if evt.get("type") in interesting
        ]
        expected_order = [
            EVT_ASR_FINAL,
            EVT_NLU,
            EVT_POLICY_DECISION,
            EVT_LLM_RESPONSE_START,
            EVT_LLM_RESPONSE_END,
            EVT_NLG,
            EVT_TTS_START,
            EVT_TTS_END,
        ]
        self.assertEqual(expected_order, ordered_types)

        finals = self._published_by_type(EVT_ASR_FINAL)
        nlus = self._published_by_type(EVT_NLU)
        policies = self._published_by_type(EVT_POLICY_DECISION)
        llm_starts = self._published_by_type(EVT_LLM_RESPONSE_START)
        llm_ends = self._published_by_type(EVT_LLM_RESPONSE_END)
        nlgs = self._published_by_type(EVT_NLG)
        tts_starts = self._published_by_type(EVT_TTS_START)
        tts_ends = self._published_by_type(EVT_TTS_END)

        self.assertEqual(1, len(finals))
        self.assertEqual(1, len(nlus))
        self.assertEqual(1, len(policies))
        self.assertEqual(1, len(llm_starts))
        self.assertEqual(1, len(llm_ends))
        self.assertEqual(1, len(nlgs))
        self.assertEqual(1, len(tts_starts))
        self.assertEqual(1, len(tts_ends))

        final_event = finals[0]
        final_req_id = final_event.get("req_id")
        self.assertIsInstance(final_req_id, str)
        self.assertIn("confidence", final_event)

        for collection in (
            nlus,
            policies,
            llm_starts,
            llm_ends,
            nlgs,
            tts_starts,
            tts_ends,
        ):
            event = collection[0]
            self.assertEqual(final_req_id, event["req_id"])


if __name__ == "__main__":
    unittest.main()
