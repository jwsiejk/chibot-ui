import asyncio
import os
import sys
import types
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
if "jwt" not in sys.modules:
    sys.modules["jwt"] = types.SimpleNamespace(
        encode=lambda *args, **kwargs: "token",
        decode=lambda *args, **kwargs: {},
        PyJWTError=Exception,
    )


class _LenientNamespace(types.SimpleNamespace):
    def __getattr__(self, name: str):  # pragma: no cover - test shim
        value = _LenientNamespace()
        setattr(self, name, value)
        return value


if "google" not in sys.modules:
    google_stub = types.SimpleNamespace()
    api_core = types.SimpleNamespace(exceptions=types.SimpleNamespace(OutOfRange=Exception))
    speech_stub = _LenientNamespace()
    sys.modules["google"] = google_stub
    sys.modules["google.api_core"] = api_core
    sys.modules["google.api_core.exceptions"] = api_core.exceptions
    sys.modules["google.cloud"] = types.SimpleNamespace(speech=speech_stub)
    sys.modules["google.cloud.speech"] = speech_stub

from app.telemetry import bus
from app.voice_v2 import EVT_WS_AUDIO_RECV
from app.ws.adapter import AdapterContext, ChatV2Adapter, PCM_BYTES_PER_SAMPLE


class _RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, int]] = []

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:  # pragma: no cover - exercised in tests
        self.calls.append((sid, bytes(chunk), seq))


class AudioJitterBufferTests(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def _ctx(self, sid: str) -> AdapterContext:
        ctx = AdapterContext(sid=sid, headers={})
        ctx.current_turn_open = True
        ctx.session.asr_state = "open"
        ctx.asr_ready = True
        ctx.client_mic_open = True
        return ctx

    def test_in_order_frames_flush_immediately(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = self._ctx("sid-in-order")

        events: list[dict] = []
        token = bus.subscribe(EVT_WS_AUDIO_RECV, events.append)
        try:
            asyncio.run(adapter._ingest_audio_chunk(ctx, b"aaaa", 0))
            asyncio.run(adapter._ingest_audio_chunk(ctx, b"bbbb", 1))
        finally:
            bus.unsubscribe(token)

        self.assertEqual([call[2] for call in engine.calls], [0, 1])
        self.assertEqual([evt["meta"]["seq"] for evt in events], [0, 1])

    def test_out_of_order_within_window_reorders(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = self._ctx("sid-reorder")

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"late", 1))
        self.assertEqual(engine.calls, [])

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"first", 0))
        self.assertEqual([call[2] for call in engine.calls], [0, 1])

    def test_gap_detection_emits_event_and_skips_missing(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = self._ctx("sid-gap")

        audio_events: list[dict] = []
        gap_events: list[dict] = []
        token_audio = bus.subscribe(EVT_WS_AUDIO_RECV, audio_events.append)
        token_gap = bus.subscribe("EVT_AUDIO_GAP", gap_events.append)
        try:
            asyncio.run(adapter._ingest_audio_chunk(ctx, b"zero", 0))
            asyncio.run(adapter._ingest_audio_chunk(ctx, b"skip", 9))
        finally:
            bus.unsubscribe(token_audio)
            bus.unsubscribe(token_gap)

        self.assertEqual([call[2] for call in engine.calls], [0, 9])
        self.assertEqual(len(gap_events), 1)
        gap_meta = gap_events[0]["meta"]
        self.assertEqual(gap_meta["from_seq"], 1)
        self.assertEqual(gap_meta["to_seq"], 9)

        self.assertEqual([evt["meta"]["seq"] for evt in audio_events], [0, 9])

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"old", 2))
        self.assertEqual([call[2] for call in engine.calls], [0, 9])

    def test_stale_frames_are_tracked(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = self._ctx("sid-stale")

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"first", 0))
        self.assertEqual(ctx.audio_stale_drop_count, 0)

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"duplicate", 0))
        self.assertEqual(ctx.audio_stale_drop_count, 1)
        self.assertEqual([call[2] for call in engine.calls], [0])

    def test_mic_buffer_capacity_respects_encoding_width(self) -> None:
        adapter = ChatV2Adapter()
        ctx = self._ctx("sid-capacity")
        ctx.audio_profile = {"sample_rate": 8000, "channels": 1, "encoding": "PCMU"}

        pcmu_capacity = adapter._mic_buffer_capacity_bytes(ctx)
        self.assertEqual(pcmu_capacity, 8000 * adapter.MIC_BUFFER_MAX_SECONDS)

        ctx.audio_profile["encoding"] = "LINEAR16"
        linear_capacity = adapter._mic_buffer_capacity_bytes(ctx)
        self.assertEqual(
            linear_capacity,
            8000 * PCM_BYTES_PER_SAMPLE * adapter.MIC_BUFFER_MAX_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
