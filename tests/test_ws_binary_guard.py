"""Tests for binary audio routing guard in the chat.v2 adapter."""
from __future__ import annotations

import asyncio
import os
import json
import uuid
from typing import Any, Dict, List

import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ALLOW_AUDIO_WITHOUT_ASR", "1")

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_READY, EVT_WS_AUDIO_RECV
from app.ws.adapter import ChatV2Adapter, RATE_LIMIT_CAPACITY
from app.security.jwt_utils import mint_ws_token


class RecordingEngine:
    """Recording stub for the engine hooks."""

    def __init__(self, adapter: ChatV2Adapter, accept_audio: bool = True) -> None:
        self.adapter = adapter
        self.accept_audio = accept_audio
        self.open_sid: str | None = None
        self.audio_calls: List[tuple[str, bytes, int]] = []

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:  # pragma: no cover - exercised via adapter
        self.open_sid = sid
        bus.publish({"type": EVT_ASR_READY, "sid": sid, "vendor": "speechmatics"})
        if not self.accept_audio:
            self.adapter.set_accepting_audio(sid, False)

    def on_json(self, sid: str, frame: Dict[str, Any]) -> None:  # pragma: no cover - signature stub
        return None

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:
        self.audio_calls.append((sid, chunk, seq))


class TestWebSocketBinaryGuard(unittest.TestCase):
    """Integration tests for the binary audio guard behavior."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    @staticmethod
    def _make_scope() -> Dict[str, Any]:
        sid = f"sid-{uuid.uuid4().hex}"
        token = mint_ws_token("user-1", sid, False)
        return {
            "type": "websocket",
            "subprotocols": ["chat.v2"],
            "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "query_string": f"access_token={token}".encode("ascii"),
        }

    async def _run_adapter(self, adapter: ChatV2Adapter, events: List[dict]) -> List[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)

        sent: List[dict] = []

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        await adapter(self._make_scope(), receive, send)
        return sent

    def _drive(self, adapter: ChatV2Adapter, events: List[dict]) -> List[dict]:
        return asyncio.run(self._run_adapter(adapter, events))

    @staticmethod
    def _extract_error_frames(messages: List[dict]) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []
        for message in messages:
            if message.get("type") != "websocket.send":
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "error":
                errors.append(payload)
        return errors

    def test_default_binary_flow_without_header(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine(adapter)
        adapter.engine = engine

        received_events: List[dict] = []
        token = bus.subscribe(EVT_WS_AUDIO_RECV, lambda event: received_events.append(event))
        try:
            payload_one = b"\x01" * 4
            payload_two = b"\x02" * 8
            events = [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "bytes": payload_one},
                {"type": "websocket.receive", "bytes": payload_two},
                {"type": "websocket.disconnect", "code": 1000},
            ]

            sent = self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        accept_messages = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accept_messages), 1)

        self.assertEqual(len(engine.audio_calls), 2)
        self.assertEqual(engine.audio_calls[0][2], 0)
        self.assertEqual(engine.audio_calls[1][2], 1)

        self.assertEqual(len(received_events), 2)
        self.assertEqual(received_events[0]["meta"]["seq"], 0)
        self.assertEqual(received_events[1]["meta"]["seq"], 1)
        self.assertEqual(received_events[0]["meta"]["ws"]["size"], len(payload_one))
        self.assertEqual(received_events[1]["meta"]["ws"]["size"], len(payload_two))

    def test_audio_header_sets_profile_and_allows_audio(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine(adapter)
        adapter.engine = engine

        received_events: List[dict] = []
        token = bus.subscribe(EVT_WS_AUDIO_RECV, lambda event: received_events.append(event))
        try:
            header = {"type": "audio.header", "format": "opus", "sample_rate": 48000, "channels": 1}
            payload = b"\x03" * 2
            events = [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "text": json.dumps(header)},
                {"type": "websocket.receive", "bytes": payload},
                {"type": "websocket.disconnect", "code": 1000},
            ]

            sent = self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        self.assertEqual(len(engine.audio_calls), 1)
        self.assertEqual(engine.audio_calls[0][2], 0)

        errors = self._extract_error_frames(sent)
        self.assertFalse(errors)

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0]["meta"]["ws"]["size"], len(payload))

    def test_duplicate_audio_header_reports_error(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine(adapter)
        adapter.engine = engine

        header_one = {"type": "audio.header", "format": "opus", "sample_rate": 48000, "channels": 1}
        header_two = {"type": "audio.header", "format": "pcm", "sample_rate": 16000, "channels": 1}
        events = [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps(header_one)},
            {"type": "websocket.receive", "text": json.dumps(header_two)},
            {"type": "websocket.disconnect", "code": 1000},
        ]

        sent = self._drive(adapter, events)

        error_messages = self._extract_error_frames(sent)
        self.assertEqual(len(error_messages), 1)
        payload = error_messages[0]
        self.assertEqual(payload, {
            "type": "error",
            "code": "schema_invalid",
            "detail": "duplicate or conflicting audio.header",
        })

        close_messages = [msg for msg in sent if msg.get("type") == "websocket.close"]
        self.assertFalse(close_messages)

    def test_not_accepting_audio_triggers_policy_close(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine(adapter, accept_audio=False)
        adapter.engine = engine

        received_events: List[dict] = []
        token = bus.subscribe(EVT_WS_AUDIO_RECV, lambda event: received_events.append(event))
        try:
            chunk = b"\x05" * 4
            events = [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "bytes": chunk},
                {"type": "websocket.receive", "bytes": chunk},
                {"type": "websocket.receive", "bytes": chunk},
            ]

            sent = self._drive(adapter, events)
        finally:
            bus.unsubscribe(token)

        self.assertFalse(engine.audio_calls)

        error_frames = self._extract_error_frames(sent)
        self.assertEqual(len(error_frames), 3)
        parsed = error_frames
        for payload in parsed[:2]:
            self.assertEqual(payload["code"], "audio_not_expected")
            self.assertEqual(payload["detail"], "engine not accepting audio")
        self.assertEqual(parsed[2]["code"], "audio_not_expected")
        self.assertEqual(parsed[2]["detail"], "engine not accepting audio")

    def test_audio_stream_not_rate_limited(self) -> None:
        adapter = ChatV2Adapter()
        engine = RecordingEngine(adapter)
        adapter.engine = engine

        chunk = b"\x07" * 160
        chunk_count = RATE_LIMIT_CAPACITY + 6

        ready_frame = {
            "type": "client.ready",
            "mic": {"state": "open", "vendor": "webm_opus", "ts": 1761889784321},
        }
        client_log_ready = {
            "type": "client.log",
            "label": "client_ready_handshake",
            "detail": {
                "outcome": "sent",
                "attempts": 1,
                "readyState": 1,
                "events": [
                    {"kind": "enqueue", "ts": 1761889784339},
                    {"kind": "attempt", "ts": 1761889784339},
                    {"kind": "sent", "ts": 1761889784339},
                ],
            },
        }

        events = [{"type": "websocket.connect"}]
        events.append({"type": "websocket.receive", "text": json.dumps(ready_frame)})
        events.append({"type": "websocket.receive", "text": json.dumps(client_log_ready)})

        for idx in range(chunk_count):
            if idx in {0, 3, 12}:
                events.append(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps(
                            {
                                "type": "client.log",
                                "label": "client_audio_stream",
                                "detail": {
                                    "chunk": idx,
                                    "size": len(chunk),
                                    "readyState": 1,
                                },
                            }
                        ),
                    }
                )
            events.append({"type": "websocket.receive", "bytes": chunk})

        events.append({"type": "websocket.disconnect", "code": 1000})

        sent = self._drive(adapter, events)

        error_frames = self._extract_error_frames(sent)
        self.assertFalse(
            any(frame.get("code") == "rate_limited" for frame in error_frames),
            msg=f"unexpected rate limit errors: {error_frames}",
        )

        self.assertEqual(len(engine.audio_calls), chunk_count)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
