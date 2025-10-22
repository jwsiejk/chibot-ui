from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Callable, Dict, List

from app.telemetry import bus
from app.voice_v2 import EVT_WS_JSON_SEND
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter


class RecordingEngine:
    """Engine stub that records the opened session identifier."""

    def __init__(self) -> None:
        self.open_sid: str | None = None

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:
        self.open_sid = sid


class ResumeHarness:
    """Helper harness for exercising resume flows."""

    def __init__(
        self,
        adapter: ChatV2Adapter,
        engine: RecordingEngine,
        *,
        query_string: str | bytes | None = None,
    ) -> None:
        self.adapter = adapter
        self.engine = engine
        self.scope: Dict[str, Any] = {
            "type": "websocket",
            "subprotocols": [CHAT_V2_SUBPROTOCOL],
            "headers": [(b"authorization", b"Bearer test-token")],
            "client": ("127.0.0.1", 1234),
        }
        if query_string is not None:
            if isinstance(query_string, str):
                query_bytes = query_string.encode("utf-8")
            else:
                query_bytes = query_string
            self.scope["query_string"] = query_bytes
        self._inbound: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: List[dict] = []
        self.outbound_frames: List[Dict[str, Any]] = []
        self.close_messages: List[dict] = []
        self.accepted = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self.adapter(self.scope, self._receive, self._send))
        await self._inbound.put({"type": "websocket.connect"})
        await self.wait_for(lambda: self.accepted or (self._task is not None and self._task.done()))

    async def _receive(self) -> dict:
        return await self._inbound.get()

    async def _send(self, message: dict) -> None:
        self.sent.append(message)
        msg_type = message.get("type")
        if msg_type == "websocket.accept":
            self.accepted = True
        elif msg_type == "websocket.send" and message.get("text") is not None:
            self.outbound_frames.append(json.loads(message["text"]))
        elif msg_type == "websocket.close":
            self.close_messages.append(message)

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for predicate")
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

    async def wait_for_frame_count(self, count: int, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self.outbound_frames) < count:
            if loop.time() >= deadline:
                raise TimeoutError("timed out waiting for outbound frames")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        if self._task is None:
            return
        await self._inbound.put({"type": "websocket.disconnect", "code": 1000})
        await self._task
        self._task = None

    async def finish(self) -> None:
        if self._task is None:
            return
        await self._task
        self._task = None

    @property
    def sid(self) -> str:
        sid = self.engine.open_sid
        if sid is None:
            raise RuntimeError("connection not yet open")
        return sid


class TestWebSocketResume(unittest.TestCase):
    """Tests covering resume token behavior for the WebSocket adapter."""

    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_info_frame_includes_resume_token(self) -> None:
        asyncio.run(self._test_info_frame_includes_resume_token())

    def test_resume_replays_markers_and_sid(self) -> None:
        asyncio.run(self._test_resume_replays_markers_and_sid())

    def test_expired_token_rejected(self) -> None:
        asyncio.run(self._test_expired_token_rejected())

    async def _test_info_frame_includes_resume_token(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = ResumeHarness(adapter, engine)
        await harness.start()
        try:
            payload = {"type": "info", "session": "abc"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": payload})

            frame = await harness.wait_for_outbound(lambda data: data.get("type") == "info")
            self.assertIn("resume_token", frame)
            self.assertEqual(frame.get("resume_ttl_ms"), 10_000)
            self.assertTrue(frame["resume_token"])
        finally:
            await harness.close()

    async def _test_resume_replays_markers_and_sid(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = ResumeHarness(adapter, engine)
        await harness.start()
        try:
            info_frame = {"type": "info"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": info_frame})
            info = await harness.wait_for_outbound(lambda data: data.get("type") == "info")
            resume_token = info["resume_token"]
            original_sid = harness.sid

            markers = [
                {"type": "tts.start", "utt_id": "utt-1"},
                {"type": "tts.end", "utt_id": "utt-1"},
                {"type": "asr.final", "req_id": "req-1", "text": "hello"},
            ]
            for marker in markers:
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": original_sid, "payload": marker})
                await harness.wait_for_outbound(lambda data, m=marker: data == m)
        finally:
            await harness.close()

        resume_scope = f"resume={resume_token}"
        resumed = ResumeHarness(adapter, engine, query_string=resume_scope)
        await resumed.start()
        try:
            self.assertEqual(resumed.sid, original_sid)

            await resumed.wait_for_frame_count(len(markers))
            self.assertEqual(resumed.outbound_frames[: len(markers)], markers)
        finally:
            await resumed.close()

    async def _test_expired_token_rejected(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        harness = ResumeHarness(adapter, engine)
        await harness.start()
        try:
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": harness.sid, "payload": {"type": "info"}})
            info = await harness.wait_for_outbound(lambda data: data.get("type") == "info")
            resume_token = info["resume_token"]
        finally:
            await harness.close()

        # Force expiry
        state = adapter._resume_tokens.get(resume_token)  # type: ignore[attr-defined]
        if state is not None:
            state.expiry_ms = adapter._now_ms() - 1  # type: ignore[attr-defined]

        resumed = ResumeHarness(adapter, engine, query_string=f"resume={resume_token}")
        await resumed.start()
        await resumed.finish()

        errors = [msg for msg in resumed.sent if msg.get("type") == "websocket.send"]
        self.assertTrue(errors, "expected error frame to be sent")
        payload = json.loads(errors[0]["text"])
        self.assertEqual(
            payload,
            {
                "type": "error",
                "code": "resume_invalid",
                "detail": "Resume token expired or invalid",
                "retryable": False,
            },
        )

        closes = [msg for msg in resumed.close_messages if msg.get("type") == "websocket.close"]
        self.assertTrue(closes, "expected close frame to be sent")
        self.assertEqual(closes[0].get("code"), 1008)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
