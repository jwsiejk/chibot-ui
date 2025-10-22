"""Tests for chat.v2 JSON contract handshakes and diagnostics."""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Callable, Dict, List

from app.telemetry import bus
from app.ws.adapter import (
    CHAT_V2_SUBPROTOCOL,
    EVT_BACKPRESSURE_OFF,
    EVT_BACKPRESSURE_ON,
    QUEUE_OFF_THRESHOLD,
    QUEUE_ON_THRESHOLD,
    ChatV2Adapter,
)


class RecordingEngine:
    """Minimal engine stub that records the opened session identifier."""

    def __init__(self) -> None:
        self.open_sid: str | None = None

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:
        self.open_sid = sid


class TestWebSocketJsonContract(unittest.TestCase):
    """Integration tests covering JSON contract behavior."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    @staticmethod
    def _make_scope(*, subprotocols: List[str] | None = None) -> Dict[str, Any]:
        return {
            "type": "websocket",
            "subprotocols": subprotocols if subprotocols is not None else [CHAT_V2_SUBPROTOCOL],
            "headers": [(b"authorization", b"Bearer test-token")],
            "client": ("127.0.0.1", 1234),
        }

    async def _run_adapter(
        self,
        adapter: ChatV2Adapter,
        events: List[dict],
        *,
        scope: Dict[str, Any] | None = None,
    ) -> List[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)

        sent: List[dict] = []

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        await adapter(scope or self._make_scope(), receive, send)
        return sent

    def _drive(
        self,
        adapter: ChatV2Adapter,
        events: List[dict],
        *,
        scope: Dict[str, Any] | None = None,
    ) -> List[dict]:
        return asyncio.run(self._run_adapter(adapter, events, scope=scope))

    def test_subprotocol_required_returns_426(self) -> None:
        adapter = ChatV2Adapter()
        scope = self._make_scope(subprotocols=[])
        events = [{"type": "websocket.connect"}]

        sent = self._drive(adapter, events, scope=scope)

        self.assertEqual(len(sent), 2)
        start, body = sent
        self.assertEqual(start["type"], "websocket.http.response.start")
        self.assertEqual(start["status"], 426)
        self.assertEqual(body["type"], "websocket.http.response.body")
        payload = json.loads(body["body"].decode("utf-8"))
        self.assertEqual(
            payload,
            {"type": "error", "code": "bad_subprotocol", "detail": "use chat.v2"},
        )

    def test_unknown_type_error_uses_standard_shape(self) -> None:
        adapter = ChatV2Adapter()
        frame = {"type": "client.foo", "payload": "ignored"}
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(frame)},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        sent = self._drive(adapter, events)
        accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accepts), 1)

        errors = [msg for msg in sent if msg.get("type") == "websocket.send"]
        self.assertEqual(len(errors), 1)
        payload = json.loads(errors[0]["text"])
        self.assertEqual(
            payload,
            {"type": "error", "code": "unknown_type", "detail": "client.foo"},
        )

    def test_backpressure_events_toggle_on_thresholds(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine()
        adapter.engine = engine

        received: List[dict] = []
        token_on = bus.subscribe(EVT_BACKPRESSURE_ON, lambda event: received.append(event))
        token_off = bus.subscribe(EVT_BACKPRESSURE_OFF, lambda event: received.append(event))
        try:
            asyncio.run(self._exercise_backpressure(adapter, engine))
        finally:
            bus.unsubscribe(token_on)
            bus.unsubscribe(token_off)

        self.assertEqual([event["type"] for event in received], [EVT_BACKPRESSURE_ON, EVT_BACKPRESSURE_OFF])
        self.assertEqual(received[0]["meta"]["queue_depth"], QUEUE_ON_THRESHOLD + 1)
        self.assertEqual(received[0]["meta"]["state"], "on")
        self.assertEqual(received[1]["meta"]["queue_depth"], QUEUE_OFF_THRESHOLD - 1)
        self.assertEqual(received[1]["meta"]["state"], "off")

    async def _exercise_backpressure(self, adapter: ChatV2Adapter, engine: RecordingEngine) -> None:
        scope = self._make_scope()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        sent: List[dict] = []

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        task = asyncio.create_task(adapter(scope, receive, send))
        await queue.put({"type": "websocket.connect"})

        await self._wait_for(lambda: engine.open_sid is not None)
        sid = engine.open_sid
        assert sid is not None

        await adapter.set_outbound_queue_depth(sid, QUEUE_ON_THRESHOLD + 1)
        await adapter.set_outbound_queue_depth(sid, QUEUE_OFF_THRESHOLD - 1)

        await queue.put({"type": "websocket.disconnect", "code": 1000})
        await task

        accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        if not accepts:
            raise AssertionError("connection was not accepted")

    @staticmethod
    async def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for predicate")
            await asyncio.sleep(0.01)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
