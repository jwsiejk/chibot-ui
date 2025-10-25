import asyncio
import time
import unittest

from app.telemetry import bus
from app.voice_v2.tts_runtime import TTSRuntime
from app.voice_v2.tts_base import TTSProviderBase


class _StubStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._chunks:
            raise StopAsyncIteration
        chunk = self._chunks.pop(0)
        await asyncio.sleep(0)
        return chunk

    async def aclose(self) -> None:
        self.closed = True


class _StubProvider(TTSProviderBase):
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        super().__init__(vendor="stub", telemetry_bus=bus, retries=0, timeout_s=1)
        if chunks is None:
            chunks = [b"\x00" * 320]
        self._chunks = list(chunks)

    async def _synthesize_impl(self, text: str, *, voice_id: str | None = None, **kwargs) -> _StubStream:
        if not voice_id:
            raise RuntimeError("voice_id required")
        return _StubStream(self._chunks)


class _StubEngine:
    def __init__(self) -> None:
        self.starts: list[tuple[str, str, int]] = []
        self.chunks: list[tuple[str, bytes]] = []
        self.ends: list[tuple[str, str, int]] = []
        self.policy_snapshot: dict[str, object] = {}

    def _voice_profile(self) -> tuple[str, str]:
        return ("stub-voice", "en-US")

    def on_tts_start(self, sid: str, utt_id: str, post_hold_ms: int | None = None) -> None:
        self.starts.append((sid, utt_id, int(post_hold_ms or 0)))

    def emit_tts_audio_chunk(self, sid: str, chunk: bytes) -> None:
        self.chunks.append((sid, bytes(chunk)))

    def on_tts_end(self, sid: str, utt_id: str, post_hold_ms: int | None = None) -> None:
        self.ends.append((sid, utt_id, int(post_hold_ms or 0)))

    def cancel_current_tts(self, sid: str, *, reason: str) -> None:  # pragma: no cover - not used
        return


class TestTTSRuntimeBackgroundLoop(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_emits_tts_events_without_running_loop(self) -> None:
        engine = _StubEngine()
        provider = _StubProvider()
        runtime = TTSRuntime(engine=engine, provider=provider, telemetry_bus=bus)

        event = {"sid": "sid-1", "text": "Hello", "req_id": "req-1"}
        runtime._handle_nlg_event(event)

        deadline = time.time() + 2.0
        while not engine.ends and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(engine.starts)
        self.assertTrue(engine.chunks)
        self.assertTrue(engine.ends)

        loop = runtime._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = runtime._loop_thread
        if thread is not None:
            thread.join(timeout=1)

    def test_buffers_partial_pcm_frames(self) -> None:
        engine = _StubEngine()
        provider = _StubProvider(chunks=[b"\x00" * 3, b"\x01" * 3, b"\x02" * 4])
        runtime = TTSRuntime(engine=engine, provider=provider, telemetry_bus=bus)

        event = {"sid": "sid-odd", "text": "Hello", "req_id": "req-odd"}
        runtime._handle_nlg_event(event)

        deadline = time.time() + 2.0
        while not engine.ends and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(engine.chunks)
        for _, chunk in engine.chunks:
            self.assertEqual(len(chunk) % 2, 0)

        total = b"".join(chunk for _, chunk in engine.chunks)
        self.assertEqual(len(total), 10)

        loop = runtime._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = runtime._loop_thread
        if thread is not None:
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
