"""Tests for binary audio routing guard in the chat.v2 adapter."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_READY, EVT_WS_AUDIO_RECV
from app.ws.adapter import ChatV2Adapter


class RecordingEngine:
    """Recording stub for the engine hooks."""

    def __init__(self, adapter: ChatV2Adapter, accept_audio: bool = True) -> None:
        self.adapter = adapter
        self.accept_audio = accept_audio
        self.open_sid: str | None = None
        self.audio_calls: List[tuple[str, bytes, int]] = []

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:  # pragma: no cover - exercised via adapter
        self.open_sid = sid
        bus.publish({"type": EVT_ASR_READY, "sid": sid, "vendor": "deepgram"})
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
        return {
            "type": "websocket",
            "subprotocols": ["chat.v2"],
            "headers": [(b"authorization", b"Bearer test-token")],
            "client": ("127.0.0.1", 12345),
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

        errors = [msg for msg in sent if msg.get("type") == "websocket.send" and msg.get("text")]
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

        error_messages = [msg for msg in sent if msg.get("type") == "websocket.send" and msg.get("text")]
        self.assertEqual(len(error_messages), 1)
        payload = json.loads(error_messages[0]["text"])
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

        error_frames = [msg for msg in sent if msg.get("type") == "websocket.send" and msg.get("text")]
        self.assertEqual(len(error_frames), 3)
        parsed = [json.loads(msg["text"]) for msg in error_frames]
        for payload in parsed[:2]:
            self.assertEqual(payload["code"], "audio_not_expected")
            self.assertEqual(payload["detail"], "engine not accepting audio")
        self.assertEqual(parsed[2]["code"], "audio_not_expected")
        self.assertEqual(parsed[2]["detail"], "engine not accepting audio")

        closes = [msg for msg in sent if msg.get("type") == "websocket.close"]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["code"], 1003)

        self.assertEqual(len(received_events), 3)
        self.assertEqual(received_events[-1]["meta"]["error"], "audio_not_expected_close")
        self.assertEqual(received_events[-1]["meta"]["ws"]["size"], len(chunk))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
