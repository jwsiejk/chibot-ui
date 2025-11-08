"""Tests for chat.v2 JSON contract handshakes and diagnostics."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from typing import Any, Callable, Dict, List
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.voice_v2 import EVT_CLIENT_AUTOSTART, EVT_CLIENT_BANNER, EVT_WS_JSON_RECV, EVT_WS_JSON_SEND
from app.security.jwt_utils import mint_ws_token
import app.ws.adapter as adapter_module
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
        sid = f"sid-{uuid.uuid4().hex}"
        token = mint_ws_token("user-1", sid, False)
        return {
            "type": "websocket",
            "subprotocols": subprotocols if subprotocols is not None else [CHAT_V2_SUBPROTOCOL],
            "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
            "client": ("127.0.0.1", 1234),
            "query_string": f"access_token={token}".encode("ascii"),
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

        send_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text") is not None
        ]
        error_payloads = [payload for payload in send_payloads if payload.get("type") == "error"]
        self.assertEqual(len(error_payloads), 1)
        payload = error_payloads[0]
        self.assertEqual(
            payload,
            {"type": "error", "code": "unknown_type", "detail": "client.foo"},
        )

    def test_asr_rearm_request_frame_is_allowed(self) -> None:
        adapter = ChatV2Adapter()
        frame = {"type": "asr.rearm.request"}
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(frame)},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        received: list[dict] = []
        token = bus.subscribe(EVT_WS_JSON_RECV, received.append)
        try:
            sent = self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accepts), 1)

        send_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text") is not None
        ]
        error_payloads = [payload for payload in send_payloads if payload.get("type") == "error"]
        self.assertEqual(error_payloads, [])

        self.assertTrue(
            any(event.get("meta", {}).get("frame_type") == "asr.rearm.request" for event in received),
            "Expected EVT_WS_JSON_RECV event for asr.rearm.request frame",
        )

    def test_banner_and_info_frame_include_current_build_id(self) -> None:
        adapter = ChatV2Adapter()
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        expected_build_id = "build-test-123"

        with patch("app.ws.adapter.current_build_id", return_value=expected_build_id):
            sent = self._drive(adapter, events)

        payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text") is not None
        ]

        banner = next((p for p in payloads if p.get("type") == "server.banner"), None)
        self.assertIsNotNone(banner, "Expected server.banner frame to be sent")
        self.assertEqual(banner["build_id"], expected_build_id)

        info = next((p for p in payloads if p.get("type") == "info"), None)
        self.assertIsNotNone(info, "Expected info frame to be sent")
        self.assertEqual(info["build_id"], expected_build_id)

    def test_client_autostart_frame_is_allowed(self) -> None:
        adapter = ChatV2Adapter()
        long_reason = "x" * 160
        frame = {
            "type": "client.autostart",
            "event": "blocked",
            "meta": {
                "trigger": "boot",
                "reason": long_reason,
                "attempt": 3,
                "active": True,
                "ignore": {"bad": "value"},
                "empty": " ",
            },
        }
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(frame)},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        recorded: list[dict] = []
        token = bus.subscribe(EVT_CLIENT_AUTOSTART, recorded.append)
        try:
            sent = self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accepts), 1)

        send_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text") is not None
        ]
        error_payloads = [payload for payload in send_payloads if payload.get("type") == "error"]
        self.assertEqual(error_payloads, [])

        self.assertEqual(len(recorded), 1)
        event = recorded[0]
        self.assertEqual(event["type"], EVT_CLIENT_AUTOSTART)
        event_meta = event.get("meta", {})
        self.assertEqual(event_meta.get("event"), "blocked")
        sanitized_meta = event_meta.get("meta", {})
        self.assertIn("trigger", sanitized_meta)
        self.assertEqual(sanitized_meta["trigger"], "boot")
        self.assertIn("reason", sanitized_meta)
        self.assertEqual(len(sanitized_meta["reason"]), 120)
        self.assertEqual(sanitized_meta["attempt"], 3)
        self.assertTrue(sanitized_meta["active"])
        self.assertNotIn("ignore", sanitized_meta)
        self.assertNotIn("empty", sanitized_meta)

    def test_rate_limit_error_includes_retry_metadata(self) -> None:
        original_capacity = adapter_module.RATE_LIMIT_CAPACITY
        original_window = adapter_module.RATE_LIMIT_WINDOW_SECONDS
        adapter_module.RATE_LIMIT_CAPACITY = 1
        adapter_module.RATE_LIMIT_WINDOW_SECONDS = 1.0
        try:
            adapter = ChatV2Adapter()
            frame = {"type": "chat.user", "text": "probe"}
            events = [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "text": json.dumps(frame)},
                {"type": "websocket.receive", "text": json.dumps(frame)},
                {"type": "websocket.disconnect", "code": 1000},
            ]

            sent = self._drive(adapter, events)
        finally:
            adapter_module.RATE_LIMIT_CAPACITY = original_capacity
            adapter_module.RATE_LIMIT_WINDOW_SECONDS = original_window

        error_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text") is not None
        ]
        rate_limited = [payload for payload in error_payloads if payload.get("code") == "rate_limited"]
        self.assertEqual(len(rate_limited), 1, msg=f"unexpected error payloads: {error_payloads}")
        payload = rate_limited[0]
        self.assertEqual(payload.get("message"), "Too many concurrent connections.")
        self.assertTrue(payload.get("retryable"))
        retry_in_ms = payload.get("retry_in_ms")
        self.assertIsInstance(retry_in_ms, int)
        self.assertGreaterEqual(retry_in_ms, 500)

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

    def test_client_banner_events_are_published(self) -> None:
        adapter = ChatV2Adapter()
        frame = {
            "type": "client.banner",
            "info": {"user_agent": "TestAgent/1.0", "viewport": {"width": 800, "height": 600}},
            "event": {
                "label": "ws.socket.open",
                "ts_ms": 1_234_567,
                "meta": {"ready_state": 1},
            },
        }
        received: List[dict] = []
        token = bus.subscribe(EVT_CLIENT_BANNER, lambda event: received.append(event))
        try:
            events = [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "text": json.dumps(frame)},
                {"type": "websocket.disconnect", "code": 1000},
            ]
            self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(event.get("type"), EVT_CLIENT_BANNER)
        meta = event.get("meta") or {}
        self.assertEqual(meta.get("label"), "ws.socket.open")
        self.assertIn("info", meta)
        self.assertIn("event_meta", meta)

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
