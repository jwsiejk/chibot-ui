"""Tests for the outbound WebSocket bridge in the chat.v2 adapter."""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Callable, Dict

from app.telemetry import bus
from app.voice_v2 import EVT_WS_AUDIO_SEND, EVT_WS_JSON_SEND
from app.ws.adapter import (
    CHAT_V2_SUBPROTOCOL,
    EVT_WS_OUTBOX_DROP,
    ChatV2Adapter,
)


class RecordingEngine:
    """Engine stub that captures the opened session identifier."""

    def __init__(self) -> None:
        self.open_sid: str | None = None

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:
        self.open_sid = sid


class OutboundHarness:
    """Helper for driving the adapter within an asyncio test."""

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
        self.binary_frames: list[bytes] = []
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
        if message.get("type") == "websocket.send" and message.get("bytes") is not None:
            self.binary_frames.append(message["bytes"])

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for condition")
            await asyncio.sleep(0.01)

    async def wait_for_outbound(
        self, predicate: Callable[[Dict[str, Any]], bool], timeout: float = 1.0
    ) -> Dict[str, Any]:
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
        sid = self.engine.open_sid
        if sid is None:
            raise RuntimeError("connection not yet open")
        return sid

    async def wait_for_binary(
        self, predicate: Callable[[bytes], bool], timeout: float = 1.0
    ) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for chunk in self.binary_frames:
                if predicate(chunk):
                    return chunk
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for binary frame")
            await asyncio.sleep(0.01)


class TestAdapterOutboundBridge(unittest.TestCase):
    """Integration tests covering the server-to-client outbound bridge."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_happy_path_delivers_policy_interaction(self) -> None:
        asyncio.run(self._test_happy_path())

    def test_chat_message_forwarded_with_matching_sid(self) -> None:
        asyncio.run(
            self._test_payload_forwarded({"type": "chat.message", "message_id": "m-1"})
        )

    def test_chat_history_forwarded_with_matching_sid(self) -> None:
        asyncio.run(
            self._test_payload_forwarded(
                {
                    "type": "chat.history",
                    "messages": [{"id": "m-1", "role": "user", "text": "hi"}],
                }
            )
        )

    def test_sid_isolation_drops_other_sessions(self) -> None:
        asyncio.run(self._test_sid_isolation())

    def test_chat_message_dropped_for_other_sessions(self) -> None:
        asyncio.run(
            self._test_payload_other_sid_drop({"type": "chat.message", "message_id": "ignored"})
        )

    def test_allow_list_blocks_unknown_types(self) -> None:
        asyncio.run(self._test_allow_list())

    def test_backpressure_reports_drops(self) -> None:
        asyncio.run(self._test_backpressure())

    def test_audio_frames_forwarded_as_binary_messages(self) -> None:
        asyncio.run(self._test_audio_forwarding())

    async def _test_happy_path(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            payload = {"type": "policy.interaction", "interaction_id": "abc123"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": payload})

            frame = await harness.wait_for_outbound(lambda data: data.get("type") == "policy.interaction")
            self.assertEqual(frame, payload)

            frames = [data for data in harness.outbound_frames if data.get("type") == "policy.interaction"]
            self.assertEqual(len(frames), 1)
        finally:
            await harness.close()

    async def _test_payload_forwarded(self, payload: Dict[str, Any]) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": payload})

            frame = await harness.wait_for_outbound(lambda data: data == payload)
            self.assertEqual(frame, payload)
        finally:
            await harness.close()

    async def _test_sid_isolation(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            payload = {"type": "policy.interaction", "interaction_id": "ignored"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": "other", "payload": payload})

            with self.assertRaises(TimeoutError):
                await harness.wait_for_outbound(lambda data: data.get("type") == "policy.interaction", timeout=0.1)
            self.assertEqual(len(harness.outbound_frames), 0)
        finally:
            await harness.close()

    async def _test_payload_other_sid_drop(self, payload: Dict[str, Any]) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": "other", "payload": payload})

            with self.assertRaises(TimeoutError):
                await harness.wait_for_outbound(
                    lambda data: data.get("type") == payload.get("type"), timeout=0.1
                )
            self.assertEqual(len(harness.outbound_frames), 0)
        finally:
            await harness.close()

    async def _test_allow_list(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            payload = {"type": "vendor.debug", "detail": "drop me"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": payload})

            with self.assertRaises(TimeoutError):
                await harness.wait_for_outbound(lambda data: data.get("type") == "vendor.debug", timeout=0.1)
            self.assertEqual(len(harness.outbound_frames), 0)
        finally:
            await harness.close()

    async def _test_backpressure(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        drop_count = 0

        def _record_drop(event: dict) -> None:
            nonlocal drop_count
            meta = event.get("meta") or {}
            dropped = meta.get("dropped")
            if isinstance(dropped, int):
                drop_count += dropped

        token = bus.subscribe(EVT_WS_OUTBOX_DROP, _record_drop)
        try:
            for idx in range(300):
                bus.publish(
                    {
                        "type": EVT_WS_JSON_SEND,
                        "sid": harness.sid,
                        "payload": {"type": "info", "seq": idx},
                    }
                )

            await harness.wait_for_outbound(lambda data: data.get("type") == "info")
            await harness.wait_for(lambda: drop_count > 0)
        finally:
            bus.unsubscribe(token)
            await harness.close()

    async def _test_audio_forwarding(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        try:
            chunk = b"\x01\x02" * 80
            event = {
                "type": EVT_WS_AUDIO_SEND,
                "sid": harness.sid,
                "chunk": chunk,
                "meta": {"byte_count": len(chunk)},
            }
            bus.publish(event)

            received = await harness.wait_for_binary(lambda data: data == chunk)
            self.assertEqual(received, chunk)
        finally:
            await harness.close()


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
