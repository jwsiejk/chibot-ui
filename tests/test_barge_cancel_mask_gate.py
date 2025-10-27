import unittest

from app.voice_v2 import EVT_MIC_GATE
from app.voice_v2.engine import CONFIRMING_BARGE, LISTENING, RESPONDING, EngineV2


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


class BargeCancelMaskGateTests(unittest.TestCase):
    def test_barge_cancel_clears_mask_and_gate(self) -> None:
        bus = _FakeBus()
        exporter = _FakeExporter()
        engine = EngineV2(exporter, telemetry_bus=bus)
        sid = "sid-bar"

        engine.policy_snapshot = {"barge_in_enabled": True}
        engine._schedule_barge_confirmation = lambda _: None  # type: ignore[assignment]

        engine.on_tts_start(sid, "utt-1")
        session = engine._ensure_session(sid)
        self.assertEqual(session.state, RESPONDING)

        engine.on_auto_barge_attempt(sid, "auto_vad")
        self.assertEqual(session.state, CONFIRMING_BARGE)

        types = [evt["type"] for evt in bus.events]
        barge_index = types.index("EVT_BARGE_IN")
        tts_end_index = types.index("EVT_TTS_END")
        mask_events = [
            (idx, evt)
            for idx, evt in enumerate(bus.events)
            if evt["type"] == "EVT_TTS_MASK" and evt.get("phase") == "off"
        ]
        self.assertTrue(mask_events)
        mask_index, _ = mask_events[0]

        self.assertLess(barge_index, tts_end_index)
        self.assertLess(tts_end_index, mask_index)

        tts_end_event = bus.events[tts_end_index]
        self.assertEqual(tts_end_event.get("reason"), "canceled")

        gate_events = [evt for evt in bus.events if evt["type"] == EVT_MIC_GATE]
        self.assertTrue(gate_events)
        latest_gate = gate_events[-1]
        reasons = latest_gate["meta"]["gate"]["reasons"]
        self.assertFalse(reasons["tts_active"])

        engine._complete_auto_barge(sid)
        self.assertEqual(session.state, LISTENING)


if __name__ == "__main__":
    unittest.main()
