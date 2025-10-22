import asyncio
import os
import unittest
from unittest.mock import patch

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_FINAL, EVT_ASR_PARTIAL, EVT_ASR_READY
from app.voice_v2.asr import ASRAdapter


class FakeWebSocket:
    """Minimal websocket stub to capture keepalive frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True
        self.close_code = 1000


class TestASRAdapterBasic(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_ready_emitted_once(self) -> None:
        asyncio.run(self._test_ready_emitted_once())

    def test_partials_then_final(self) -> None:
        asyncio.run(self._test_partials_then_final())

    def test_stop_cancels_keepalive(self) -> None:
        asyncio.run(self._test_stop_cancels_keepalive())

    async def _test_ready_emitted_once(self) -> None:
        ws = FakeWebSocket()
        adapter = ASRAdapter()
        events: list[dict] = []
        token = bus.subscribe("*", events.append)
        try:
            await adapter.start(ws)
            ready_events = [evt for evt in events if evt["type"] == EVT_ASR_READY]
            self.assertEqual(len(ready_events), 1)
            await adapter.stop()
        finally:
            bus.unsubscribe(token)

    async def _test_partials_then_final(self) -> None:
        ws = FakeWebSocket()
        adapter = ASRAdapter()
        events: list[dict] = []
        token = bus.subscribe("*", events.append)
        try:
            await adapter.start(ws)
            adapter.feed(b"hello", seq=0)
            adapter.feed(b"world", seq=1)

            partials = [evt for evt in events if evt["type"] == EVT_ASR_PARTIAL]
            finals = [evt for evt in events if evt["type"] == EVT_ASR_FINAL]
            self.assertGreaterEqual(len(partials), 2)
            self.assertEqual(len(finals), 1)

            req_ids = {evt["req_id"] for evt in partials}
            req_ids.add(finals[0]["req_id"])
            self.assertEqual(len(req_ids), 1)

            # New audio should start a fresh turn.
            adapter.feed(b"again", seq=2)
            adapter.feed(b"done", seq=3)
            finals = [evt for evt in events if evt["type"] == EVT_ASR_FINAL]
            self.assertEqual(len(finals), 2)
            await adapter.stop()
        finally:
            bus.unsubscribe(token)

    async def _test_stop_cancels_keepalive(self) -> None:
        ws = FakeWebSocket()
        adapter = ASRAdapter()
        with patch.dict(os.environ, {"DG_KEEPALIVE_INTERVAL_S": "0.05"}, clear=False):
            await adapter.start(ws)
            await asyncio.sleep(0.12)
            count_before_close = len(ws.sent)
            self.assertGreaterEqual(count_before_close, 1)
            await adapter.stop()
            await asyncio.sleep(0.06)
            self.assertEqual(count_before_close, len(ws.sent))
            self.assertTrue(ws.closed)
