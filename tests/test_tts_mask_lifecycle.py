import unittest

from app.voice_v2 import EVT_MIC_GATE
from app.voice_v2.engine import EngineV2


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


class TtsMaskLifecycleTests(unittest.TestCase):
    def test_tts_mask_breadcrumb_sequence(self) -> None:
        bus = _FakeBus()
        exporter = _FakeExporter()
        engine = EngineV2(exporter, telemetry_bus=bus)

        sid = "sid-123"
        utt_id = "utt-456"

        engine.on_tts_start(sid, utt_id)

        mask_events = [evt for evt in bus.events if evt["type"] == "EVT_TTS_MASK"]
        self.assertEqual(len(mask_events), 1)
        self.assertEqual(mask_events[0]["phase"], "engaged")

        gate_events = [evt for evt in bus.events if evt["type"] == EVT_MIC_GATE]
        self.assertGreaterEqual(len(gate_events), 1)
        engaged_gate = gate_events[-1]["meta"]["gate"]
        engaged_effective = engaged_gate.get("effective", engaged_gate["mask"])
        self.assertTrue(engaged_effective)
        self.assertIn("tts_active", engaged_gate["reasons"])
        self.assertTrue(engaged_gate["reasons"]["tts_active"])

        engine.on_tts_end(sid, utt_id)

        mask_events = [evt for evt in bus.events if evt["type"] == "EVT_TTS_MASK"]
        self.assertEqual([evt["phase"] for evt in mask_events], ["engaged", "off"])

        final_gate = [evt for evt in bus.events if evt["type"] == EVT_MIC_GATE][-1]
        final_gate_meta = final_gate["meta"]["gate"]
        final_effective = final_gate_meta.get("effective", final_gate_meta["mask"])
        self.assertFalse(final_effective)
        self.assertFalse(final_gate_meta["reasons"]["tts_active"])


if __name__ == "__main__":
    unittest.main()
