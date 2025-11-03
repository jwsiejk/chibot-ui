"""Tests covering ASR runtime vendor lifecycle logging."""
from __future__ import annotations

import unittest
from unittest import mock

from app.telemetry import bus
from app.voice_v2.asr_runtime import ASRRuntime, _SessionState
from app.voice_v2.engine import EngineV2


class _StubClient:
    """Stub speechmatics client that records audio writes."""

    vendor = "speechmatics"

    def __init__(self) -> None:
        self.sent_audio: list[tuple[str, bytes]] = []
        self.open_stream_calls: list[dict] = []
        self.closed_sid: str | None = None

    async def open_stream(
        self,
        sid: str,
        *,
        on_partial,
        on_final,
        on_error,
        stream_id: str,
        on_close,
        policy,
    ) -> object:
        self.open_stream_calls.append({"sid": sid, "stream_id": stream_id})
        return object()

    def send_audio(self, sid: str, chunk: bytes) -> None:
        self.sent_audio.append((sid, bytes(chunk)))

    def close_stream(self, sid: str) -> None:  # pragma: no cover - defensive stub
        self.closed_sid = sid


class TestAsrRuntimeVendorLifecycle(unittest.TestCase):
    """Runtime tests verifying vendor first-write and totals logging."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_logs_first_write_and_totals(self) -> None:
        engine = mock.create_autospec(EngineV2, instance=True)
        engine.policy_snapshot = None
        client = _StubClient()
        runtime = ASRRuntime(engine, client, telemetry_bus=bus)

        sid = "sid-vendor-logging"
        state = _SessionState(sid=sid)
        state.listening = True
        state.stream_open = True
        state.stream_id = "stream-1"
        runtime._sessions[sid] = state

        chunk = b"\x10\x11" * 64

        with self.assertLogs("app.voice_v2.asr_runtime", level="INFO") as ingest_logs:
            runtime.on_ws_audio(sid, chunk)

        self.assertTrue(
            any("evt=asr_vendor_first_write" in message for message in ingest_logs.output),
            msg=f"missing first-write log: {ingest_logs.output}",
        )
        self.assertTrue(state.vendor_first_write_logged)
        self.assertEqual(client.sent_audio, [(sid, chunk)])
        self.assertEqual(state.bytes_sent, len(chunk))

        close_cb = runtime._make_close_cb(sid, state)
        with self.assertLogs("app.voice_v2.asr_runtime", level="INFO") as close_logs:
            close_cb(None, None)

        totals = [msg for msg in close_logs.output if "evt=asr_vendor_bytes_total" in msg]
        self.assertTrue(totals, msg=f"missing totals log: {close_logs.output}")
        total_msg = totals[0]
        self.assertIn(f"bytes_total={len(chunk)}", total_msg)
        self.assertGreater(state.bytes_sent, 0)
        self.assertTrue(state.vendor_totals_logged)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
