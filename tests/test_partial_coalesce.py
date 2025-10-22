"""Partial ASR coalescing contract tests."""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Callable, Dict

from app.telemetry import bus
from app.voice_v2 import EVT_WS_JSON_SEND
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter


class RecordingEngine:
    """Minimal engine stub that records the opened session id."""

    def __init__(self) -> None:
        self.open_sid: str | None = None

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:
        self.open_sid = sid


class OutboundHarness:
    """Helper harness that drives the adapter inside asyncio tests."""

    def __init__(self, adapter: ChatV2Adapter, engine: RecordingEngine) -> None:
        self.adapter = adapter
        self.engine = engine
        self.scope = {
            "type": "websocket",
            "subprotocols": [CHAT_V2_SUBPROTOCOL],
            "headers": [(b"authorization", b"Bearer test-token")],
            "client": ("127.0.0.1", 1234),
        }
        self._inbound: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.outbound_frames: list[Dict[str, Any]] = []
        self.accepted = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self.adapter(self.scope, self._receive, self._send))
        await self._inbound.put({"type": "websocket.connect"})
        await self.wait_for(lambda: self.accepted)
        await self.wait_for(lambda: self.engine.open_sid is not None)

    async def _receive(self) -> dict:
        return await self._inbound.get()

    async def _send(self, message: dict) -> None:
        self.sent.append(message)
        if message.get("type") == "websocket.accept":
            self.accepted = True
        if message.get("type") == "websocket.send" and message.get("text") is not None:
            self.outbound_frames.append(json.loads(message["text"]))

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for condition")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        if self._task is None:
            return
        await self._inbound.put({"type": "websocket.disconnect", "code": 1000})
        await self._task
        self._task = None

    @property
    def sid(self) -> str:
        sid = self.engine.open_sid
        if sid is None:
            raise RuntimeError("connection not yet open")
        return sid


class TestPartialCoalescing(unittest.TestCase):
    """Ensure partial ASR frames are coalesced under load."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_partials_are_rate_limited_and_monotonic(self) -> None:
        asyncio.run(self._run_partial_coalesce_smoke())

    async def _run_partial_coalesce_smoke(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            for idx in range(100):
                payload = {"type": "asr.partial", "text": f"partial-{idx}"}
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": payload})
                await asyncio.sleep(0.001)

            await asyncio.sleep(0.2)

            partial_frames = [frame for frame in harness.outbound_frames if frame.get("type") == "asr.partial"]
            self.assertGreaterEqual(len(partial_frames), 2)
            self.assertLessEqual(len(partial_frames), 4)
            self.assertEqual(partial_frames[-1].get("text"), "partial-99")

            sequences = [frame.get("partial_seq") for frame in partial_frames]
            self.assertTrue(all(isinstance(seq, int) for seq in sequences))
            self.assertTrue(all(earlier < later for earlier, later in zip(sequences, sequences[1:])))
        finally:
            await harness.close()
