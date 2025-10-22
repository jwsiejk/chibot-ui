import unittest

from app.voice_v2 import EVT_TTS_START, EVT_WS_JSON_SEND
from app.voice_v2.engine import EngineV2


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:  # pragma: no cover - helper used in tests
        self.events.append(dict(event))


class TestVoiceLocale(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _RecordingBus()
        self.engine = EngineV2(telemetry_bus=self.bus)
        self.sid = "voice-locale"

    def _info_frames(self) -> list[dict]:
        frames: list[dict] = []
        for event in self.bus.events:
            if event.get("type") != EVT_WS_JSON_SEND:
                continue
            frame = event.get("frame")
            if isinstance(frame, dict) and frame.get("type") == "info":
                frames.append(frame)
        return frames

    def _tts_start_events(self) -> list[dict]:
        return [evt for evt in self.bus.events if evt.get("type") == EVT_TTS_START]

    def test_info_frame_includes_voice_and_locale(self) -> None:
        self.engine.on_open(self.sid, {})

        info_frames = self._info_frames()
        self.assertTrue(info_frames, "expected at least one info frame")

        frame = info_frames[-1]
        self.assertEqual(frame.get("voice_id"), "alloy-en-US-001")
        self.assertEqual(frame.get("locale"), "en-US")

    def test_tts_start_meta_matches_info(self) -> None:
        self.engine.on_open(self.sid, {})
        self.engine.on_tts_start(self.sid, "utt-1")

        info_frames = self._info_frames()
        self.assertTrue(info_frames, "expected an info frame before tts.start")
        info_voice = info_frames[-1].get("voice_id")
        info_locale = info_frames[-1].get("locale")

        tts_events = self._tts_start_events()
        self.assertTrue(tts_events, "expected a tts.start event")
        meta = tts_events[-1].get("meta") or {}

        self.assertEqual(meta.get("voice_id"), info_voice)
        self.assertEqual(meta.get("locale"), info_locale)

        tts_meta = meta.get("tts")
        self.assertIsInstance(tts_meta, dict)
        self.assertEqual(tts_meta.get("utt_id"), "utt-1")

    def test_policy_snapshot_overrides_defaults(self) -> None:
        self.engine.on_open(self.sid, {})

        self.engine.policy_snapshot = {
            "voice": {"voice_id": "nova-en-GB-002", "locale": "en-GB"}
        }
        self.engine._emit_info_frame(self.sid)
        self.engine.on_tts_start(self.sid, "utt-policy")

        info_frame = self._info_frames()[-1]
        tts_meta = self._tts_start_events()[-1].get("meta") or {}

        self.assertEqual(info_frame.get("voice_id"), "nova-en-GB-002")
        self.assertEqual(info_frame.get("locale"), "en-GB")
        self.assertEqual(tts_meta.get("voice_id"), "nova-en-GB-002")
        self.assertEqual(tts_meta.get("locale"), "en-GB")


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
