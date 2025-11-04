from __future__ import annotations

import asyncio
import unittest

from app.services.streaming_asr.speechmatics_client import SpeechmaticsConfigError
from app.voice_v2.asr_runtime import ASRRuntime, _SessionState


class _StubBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(dict(event))

    def subscribe(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return object()


class _StubEngine:
    def __init__(self) -> None:
        self.policy_snapshot = {}
        self.partial_calls: list[tuple[str, str, float, str]] = []
        self.final_calls: list[tuple[str, str, str | None]] = []

    def on_asr_partial(
        self, sid: str, req_id: str, confidence: float, text: str
    ) -> None:
        self.partial_calls.append((sid, req_id, confidence, text))

    def on_asr_final(self, sid: str, text: str, req_id: str | None = None) -> None:
        self.final_calls.append((sid, text, req_id))


class _StubClient:
    vendor = "speechmatics"
    idle_close_ms = 15000

    def close_stream(self, _sid: str) -> None:  # pragma: no cover - test stub
        return


class _FakeHandle:
    def __init__(self, callback):
        self._callback = callback
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def fire(self) -> None:
        if not self._cancelled:
            self._callback()


class _FakeLoop:
    def __init__(self) -> None:
        self.handles: list[_FakeHandle] = []

    def call_later(self, _delay: float, callback):  # type: ignore[no-untyped-def]
        handle = _FakeHandle(callback)
        self.handles.append(handle)
        return handle


class SpeechmaticsFinalAggregationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _StubEngine()
        self.bus = _StubBus()
        self.client = _StubClient()
        self.runtime = ASRRuntime(self.engine, self.client, telemetry_bus=self.bus)
        self.runtime._loop = _FakeLoop()

    def _prepare_state(self, sid: str) -> None:
        self.runtime._on_partial(sid, "seed", {})
        state = self.runtime._sessions[sid]
        state.final_guard_ms = 10
        state.min_segment_ms = 0

    def test_accumulates_incremental_segments(self) -> None:
        sid = "sid-incremental"
        self._prepare_state(sid)
        state = self.runtime._sessions[sid]

        for piece in ["Hello ", "world", "!"]:
            self.runtime._on_final(sid, piece, {})

        handle = state.pending_final_handle
        self.assertIsNotNone(handle)
        self.assertEqual(state.final_accumulated, "Hello world!")

        if isinstance(handle, _FakeHandle):
            handle.fire()
        else:  # pragma: no cover - defensive
            handle()

        self.assertTrue(self.engine.final_calls)
        self.assertEqual(self.engine.final_calls[-1][1], "Hello world!")
        self.assertEqual(state.final_accumulated, "")

    def test_superset_updates_replace_transcript(self) -> None:
        sid = "sid-superset"
        self._prepare_state(sid)
        state = self.runtime._sessions[sid]

        self.runtime._on_final(sid, "Hello", {})
        self.runtime._on_final(sid, "Hello world", {})
        self.runtime._on_final(sid, "Hello world", {})

        handle = state.pending_final_handle
        self.assertIsNotNone(handle)
        self.assertEqual(state.final_accumulated, "Hello world")

        if isinstance(handle, _FakeHandle):
            handle.fire()
        else:  # pragma: no cover - defensive
            handle()

        self.assertTrue(self.engine.final_calls)
        self.assertEqual(self.engine.final_calls[-1][1], "Hello world")
        self.assertEqual(state.final_accumulated, "")


class SpeechmaticsOpenValidationTest(unittest.TestCase):
    def test_invalid_config_emits_error_frame(self) -> None:
        bus = _StubBus()
        engine = _StubEngine()

        class _ConfigErrorClient(_StubClient):
            async def open_stream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise SpeechmaticsConfigError(
                    language="es",
                    sample_rate=8000,
                    encoding="linear16",
                )

            def send_audio(self, sid: str, chunk: bytes) -> None:  # pragma: no cover - stub
                return

        client = _ConfigErrorClient()
        runtime = ASRRuntime(engine, client, telemetry_bus=bus)

        async def _exercise() -> None:
            await runtime.open_if_needed("sid-invalid")

        asyncio.run(_exercise())

        state = runtime._sessions.get("sid-invalid")
        self.assertIsNotNone(state)
        if state is not None:
            self.assertFalse(state.stream_open)
            self.assertTrue(state.open_invalid)

        error_events = [
            evt
            for evt in bus.events
            if evt.get("type") == "EVT_WS_JSON_SEND"
            and evt.get("sid") == "sid-invalid"
            and isinstance(evt.get("payload"), dict)
            and evt["payload"].get("type") == "error"
        ]
        self.assertTrue(error_events)
        payload = error_events[-1]["payload"]
        self.assertEqual(payload.get("code"), "asr_open_invalid")
        self.assertIsInstance(payload.get("detail"), str)


class SpeechmaticsPreReadyAudioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = _StubBus()
        self.engine = _StubEngine()
        self.client = _StubClient()
        self.runtime = ASRRuntime(self.engine, self.client, telemetry_bus=self.bus)

    def test_drops_pending_audio_logs_warning(self) -> None:
        sid = "sid-pre-ready"
        state = _SessionState(sid=sid)
        state.stream_id = "stream-pre"
        chunk_a = b"\x00" * 1600
        chunk_b = b"\x01" * 800
        state.pending.append(chunk_a)
        state.pending.append(chunk_b)
        state.buffered_bytes = len(chunk_a) + len(chunk_b)
        self.runtime._sessions[sid] = state

        dropped = self.runtime._drain_pre_ready_audio(sid, state, state.stream_id)

        self.assertEqual(dropped, len(chunk_a) + len(chunk_b))
        self.assertFalse(state.pending)
        self.assertEqual(state.buffered_bytes, 0)
        self.assertEqual(state.dropped_chunks, 2)
        self.assertTrue(state.early_audio_dropped)
        self.assertEqual(state.early_audio_dropped_bytes, dropped)

        log_events = [
            evt
            for evt in self.bus.events
            if evt.get("type") == "EVT_LOG"
            and evt.get("sid") == sid
            and isinstance(evt.get("msg"), str)
        ]
        self.assertTrue(
            any(
                event["msg"].startswith("evt=audio_dropped_before_asr_open")
                for event in log_events
            )
        )


if __name__ == "__main__":
    unittest.main()
