import asyncio
import json
import os
import unittest
from unittest.mock import patch

from app.services.streaming_asr.deepgram_client import DeepgramClient
from app.telemetry import bus
from app.voice_v2 import EVT_WS_JSON_SEND
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter


class FakeDeepgramWebSocket:
    """Minimal websocket stub that records text frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True
        self.close_code = 1000


class LabelRecordingEngine:
    """Engine stub that records session identifiers by label."""

    def __init__(self, on_open_hook=None) -> None:
        self.sid_by_label: dict[str, str] = {}
        self._on_open_hook = on_open_hook

    def on_open(self, sid: str, headers: dict[str, str]) -> None:
        label = headers.get("x-test-label")
        if label:
            self.sid_by_label[label] = sid
        if self._on_open_hook is not None:
            self._on_open_hook(sid, headers)


class AdapterHarness:
    """Drive the adapter inside asyncio tests and record outbound frames."""

    def __init__(self, adapter: ChatV2Adapter, engine: LabelRecordingEngine, *, label: str) -> None:
        self.adapter = adapter
        self.engine = engine
        self.label = label
        self.scope = {
            "type": "websocket",
            "subprotocols": [CHAT_V2_SUBPROTOCOL],
            "headers": [
                (b"authorization", b"Bearer test-token"),
                (b"x-test-label", label.encode("ascii")),
            ],
            "client": ("127.0.0.1", 1234),
        }
        self._inbound: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.outbound_frames: list[dict] = []
        self.accepted = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self.adapter(self.scope, self._receive, self._send))
        await self._inbound.put({"type": "websocket.connect"})
        await self.wait_for(lambda: self.accepted)
        await self.wait_for(lambda: self.label in self.engine.sid_by_label)

    async def _receive(self) -> dict:
        return await self._inbound.get()

    async def _send(self, message: dict) -> None:
        self.sent.append(message)
        if message.get("type") == "websocket.accept":
            self.accepted = True
        if message.get("type") == "websocket.send" and message.get("text") is not None:
            self.outbound_frames.append(json.loads(message["text"]))

    async def wait_for(self, predicate, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for condition")
            await asyncio.sleep(0.01)

    async def wait_for_outbound(self, predicate, timeout: float = 1.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for frame in self.outbound_frames:
                if predicate(frame):
                    return frame
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for outbound frame")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        if self._task is None:
            return
        await self._inbound.put({"type": "websocket.disconnect", "code": 1000})
        await self._task
        self._task = None

    @property
    def sid(self) -> str:
        sid = self.engine.sid_by_label.get(self.label)
        if sid is None:
            raise RuntimeError("connection not yet open")
        return sid


class TestWSKeepaliveAndIsolation(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_deepgram_keepalive_cadence(self) -> None:
        asyncio.run(self._test_deepgram_keepalive_cadence())

    def test_server_keepalive_cadence(self) -> None:
        asyncio.run(self._test_server_keepalive_cadence())

    def test_initial_policy_ordering(self) -> None:
        asyncio.run(self._test_initial_policy_ordering())

    def test_multi_session_isolation(self) -> None:
        asyncio.run(self._test_multi_session_isolation())

    async def _test_deepgram_keepalive_cadence(self) -> None:
        fake_ws = FakeDeepgramWebSocket()
        with patch.dict(os.environ, {"DG_KEEPALIVE_INTERVAL_S": "0.05"}, clear=False):
            client = DeepgramClient()
            await client.connect(fake_ws)
            await asyncio.sleep(0.12)
            count_before_close = self._count_keepalives(fake_ws)
            self.assertGreaterEqual(count_before_close, 2)
            await client.close()
            await asyncio.sleep(0.06)
            self.assertEqual(count_before_close, self._count_keepalives(fake_ws))
            self.assertTrue(fake_ws.closed)

    async def _test_server_keepalive_cadence(self) -> None:
        engine = LabelRecordingEngine()
        with patch.dict(os.environ, {"WS_PING_INTERVAL_MS": "50"}, clear=False):
            adapter = ChatV2Adapter(engine=engine)
            harness = AdapterHarness(adapter, engine, label="srv")
            await harness.start()
            try:
                await asyncio.sleep(0.12)
                keepalive_frames = [
                    json.loads(message["text"])
                    for message in harness.sent
                    if message.get("type") == "websocket.send" and message.get("text")
                ]
                count = sum(1 for frame in keepalive_frames if frame.get("type") == "keepalive")
                self.assertGreaterEqual(count, 1)
                count_before_close = count
            finally:
                await harness.close()
            await asyncio.sleep(0.06)
            keepalive_frames = [
                json.loads(message["text"])
                for message in harness.sent
                if message.get("type") == "websocket.send" and message.get("text")
            ]
            count_after_close = sum(1 for frame in keepalive_frames if frame.get("type") == "keepalive")
            self.assertEqual(count_before_close, count_after_close)

    async def _test_initial_policy_ordering(self) -> None:
        snapshot = {"type": "policy.interaction", "policy": {"mode": "idle"}}

        def _on_open(sid: str, _headers: dict[str, str]) -> None:
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": snapshot})

        engine = LabelRecordingEngine(on_open_hook=_on_open)
        with patch.dict(os.environ, {"WS_PING_INTERVAL_MS": "0"}, clear=False):
            adapter = ChatV2Adapter(engine=engine)
            harness = AdapterHarness(adapter, engine, label="policy")
            await harness.start()
            try:
                frame = await harness.wait_for_outbound(lambda item: item.get("type") == "policy.interaction")
                self.assertEqual(frame, snapshot)
                await asyncio.sleep(0.05)
                frames = [f for f in harness.outbound_frames if f.get("type") == "policy.interaction"]
                self.assertEqual(len(frames), 1)
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": snapshot})
                await asyncio.sleep(0.05)
                frames = [f for f in harness.outbound_frames if f.get("type") == "policy.interaction"]
                self.assertEqual(len(frames), 1)
            finally:
                await harness.close()

    async def _test_multi_session_isolation(self) -> None:
        engine = LabelRecordingEngine()
        with patch.dict(os.environ, {"WS_PING_INTERVAL_MS": "0"}, clear=False):
            adapter = ChatV2Adapter(engine=engine)
            harness_a = AdapterHarness(adapter, engine, label="A")
            harness_b = AdapterHarness(adapter, engine, label="B")
            await harness_a.start()
            await harness_b.start()
            try:
                payload_a = {"type": "info", "label": "A"}
                payload_b = {"type": "info", "label": "B"}
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness_a.sid, "payload": payload_a})
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness_b.sid, "payload": payload_b})
                frame_a = await harness_a.wait_for_outbound(
                    lambda item: item.get("type") == "info" and item.get("label") == "A"
                )
                frame_b = await harness_b.wait_for_outbound(
                    lambda item: item.get("type") == "info" and item.get("label") == "B"
                )
                self.assertEqual(frame_a.get("label"), "A")
                self.assertEqual(frame_b.get("label"), "B")
                self.assertTrue(
                    all(frame.get("label") != "B" for frame in harness_a.outbound_frames)
                )
                self.assertTrue(
                    all(frame.get("label") != "A" for frame in harness_b.outbound_frames)
                )
            finally:
                await harness_a.close()
                await harness_b.close()

    @staticmethod
    def _count_keepalives(fake_ws: FakeDeepgramWebSocket) -> int:
        return sum(
            1
            for message in fake_ws.sent
            if json.loads(message).get("type") == "KeepAlive"
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
