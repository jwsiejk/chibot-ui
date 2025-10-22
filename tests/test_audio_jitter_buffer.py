import asyncio
import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_WS_AUDIO_RECV
from app.ws.adapter import AdapterContext, ChatV2Adapter


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

    def test_in_order_frames_flush_immediately(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = AdapterContext(sid="sid-in-order", headers={})

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
        ctx = AdapterContext(sid="sid-reorder", headers={})

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"late", 1))
        self.assertEqual(engine.calls, [])

        asyncio.run(adapter._ingest_audio_chunk(ctx, b"first", 0))
        self.assertEqual([call[2] for call in engine.calls], [0, 1])

    def test_gap_detection_emits_event_and_skips_missing(self) -> None:
        adapter = ChatV2Adapter()
        engine = _RecordingEngine()
        adapter.engine = engine
        ctx = AdapterContext(sid="sid-gap", headers={})

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


if __name__ == "__main__":
    unittest.main()
