from __future__ import annotations

import unittest

from app.voice_v2 import EVT_WS_JSON_SEND
from app.voice_v2.engine import EngineV2


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:  # pragma: no cover - helper
        self.events.append(dict(event))


class TestInfoSLO(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _RecordingBus()
        self.engine = EngineV2(telemetry_bus=self.bus)
        self.sid = "info-slo"

    def _info_frames(self) -> list[dict]:
        frames: list[dict] = []
        for event in self.bus.events:
            if event.get("type") != EVT_WS_JSON_SEND:
                continue
            frame = event.get("frame")
            if isinstance(frame, dict) and frame.get("type") == "info":
                frames.append(frame)
        return frames

    def test_info_frame_contains_slo_targets(self) -> None:
        self.engine.on_open(self.sid, {})

        info_frames = self._info_frames()
        self.assertTrue(info_frames, "expected at least one info frame")

        slo = info_frames[-1].get("slo")
        self.assertIsInstance(slo, dict)

        expected = {
            "first_partial_ms": {"target": 450, "p95": 750},
            "final_ms": {"target": 2000, "p95": 3000},
            "tts_start_ms": {"target": 350, "p95": 600},
        }

        self.assertEqual(slo, expected)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
