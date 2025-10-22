import asyncio
import unittest

from app.voice_v2 import EVT_MIC_GATE, EVT_TTS_END, EVT_TTS_START
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


class TtsMaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_start_end_with_post_hold(self) -> None:
        bus = _FakeBus()
        exporter = _FakeExporter()
        engine = EngineV2(exporter, telemetry_bus=bus)

        sid = "sid-123"
        utt_id = "utt-1"

        engine.on_tts_start(sid, utt_id, post_hold_ms=200)
        engine.on_tts_end(sid, utt_id, post_hold_ms=200)

        await asyncio.sleep(0.25)

        gate_events = [evt for evt in bus.events if evt["type"] == EVT_MIC_GATE]
        tts_start_events = [evt for evt in bus.events if evt["type"] == EVT_TTS_START]
        tts_end_events = [evt for evt in bus.events if evt["type"] == EVT_TTS_END]

        self.assertGreaterEqual(len(gate_events), 3)

        first_gate = gate_events[0]["meta"]["gate"]
        self.assertEqual(first_gate["state"], "on")
        self.assertEqual(first_gate["reason"], "tts_active")

        self.assertTrue(
            any(evt["meta"]["gate"]["reason"] == "system_hold" for evt in gate_events)
        )
        self.assertEqual(gate_events[-1]["meta"]["gate"]["state"], "off")

        self.assertEqual(len(tts_start_events), 1)
        start_meta = tts_start_events[0]["meta"]["tts"]
        self.assertEqual(start_meta["utt_id"], utt_id)
        self.assertEqual(start_meta["post_hold_ms"], 200)

        self.assertEqual(len(tts_end_events), 1)
        end_meta = tts_end_events[0]["meta"]["tts"]
        self.assertEqual(end_meta["utt_id"], utt_id)

    async def test_tts_end_no_post_hold(self) -> None:
        bus = _FakeBus()
        exporter = _FakeExporter()
        engine = EngineV2(exporter, telemetry_bus=bus)

        sid = "sid-456"
        utt_id = "utt-2"

        engine.on_tts_start(sid, utt_id, post_hold_ms=0)
        engine.on_tts_end(sid, utt_id, post_hold_ms=0)

        await asyncio.sleep(0)

        gate_events = [evt for evt in bus.events if evt["type"] == EVT_MIC_GATE]
        reasons = [evt["meta"]["gate"]["reason"] for evt in gate_events]

        self.assertFalse(any(reason == "system_hold" for reason in reasons))
        self.assertEqual(gate_events[-1]["meta"]["gate"]["state"], "off")

        tts_end_events = [evt for evt in bus.events if evt["type"] == EVT_TTS_END]
        self.assertEqual(len(tts_end_events), 1)
        self.assertEqual(tts_end_events[0]["meta"]["tts"]["utt_id"], utt_id)


if __name__ == "__main__":
    unittest.main()
