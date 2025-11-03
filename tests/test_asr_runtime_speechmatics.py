from __future__ import annotations

import unittest

from app.voice_v2.asr_runtime import ASRRuntime


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


if __name__ == "__main__":
    unittest.main()
