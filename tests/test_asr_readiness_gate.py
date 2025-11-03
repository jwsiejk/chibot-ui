import asyncio
import json
import os
import time
import unittest
import uuid
from typing import Any, Dict, List

from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_READY, EVT_WS_AUDIO_RECV
from app.ws.adapter import ChatV2Adapter
from app.security.jwt_utils import mint_ws_token


class RecordingEngine:
    """Recording stub to observe ASR readiness behavior."""

    def __init__(self) -> None:
        self.open_sid: str | None = None
        self.audio_calls: List[tuple[str, bytes, int]] = []

    def on_open(self, sid: str, headers: Dict[str, str]) -> None:  # pragma: no cover - exercised via adapter
        self.open_sid = sid

    def on_json(self, sid: str, frame: Dict[str, Any]) -> None:  # pragma: no cover - signature stub
        return None

    def on_audio(self, sid: str, chunk: bytes, seq: int) -> None:
        self.audio_calls.append((sid, bytes(chunk), seq))


class TestASRReadinessGate(unittest.TestCase):
    """Integration tests covering the ASR readiness gate."""

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

    async def _exercise(self, publish_ready: bool) -> tuple[List[dict], RecordingEngine, List[dict]]:
        adapter = ChatV2Adapter()
        engine = RecordingEngine()
        adapter.engine = engine

        scope = self._make_scope()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        sent: List[dict] = []
        audio_events: List[dict] = []
        token = bus.subscribe(EVT_WS_AUDIO_RECV, audio_events.append)

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        try:
            task = asyncio.create_task(adapter(scope, receive, send))
            await queue.put({"type": "websocket.connect"})

            async def wait_for(predicate, timeout: float = 0.5) -> None:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                while not predicate():
                    if loop.time() >= deadline:
                        raise TimeoutError("timed out waiting for condition")
                    await asyncio.sleep(0.01)

            await wait_for(lambda: engine.open_sid is not None)

            if publish_ready:
                sid = engine.open_sid
                if sid is None:  # pragma: no cover - defensive
                    raise RuntimeError("sid not set")
                bus.publish({"type": EVT_ASR_READY, "sid": sid, "vendor": "speechmatics"})

            chunk = b"\x00" * 4
            await queue.put({"type": "websocket.receive", "bytes": chunk})
            await asyncio.sleep(0.05)

            if publish_ready:
                await queue.put({"type": "websocket.disconnect", "code": 1000})

            try:
                await asyncio.wait_for(task, timeout=0.5)
            except asyncio.TimeoutError:  # pragma: no cover - defensive
                task.cancel()
                raise
        finally:
            bus.unsubscribe(token)

        return sent, engine, audio_events

    async def _exercise_speechmatics_concurrency(self) -> tuple[List[dict], Dict[str, Any]]:
        adapter = ChatV2Adapter()
        engine = RecordingEngine()
        adapter.engine = engine

        scope = self._make_scope()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        sent: List[dict] = []

        async def receive() -> dict:
            return await queue.get()

        async def send(message: dict) -> None:
            sent.append(message)

        task = asyncio.create_task(adapter(scope, receive, send))
        await queue.put({"type": "websocket.connect"})

        async def wait_for(predicate, timeout: float = 0.5) -> None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while not predicate():
                if loop.time() >= deadline:
                    raise TimeoutError("timed out waiting for condition")
                await asyncio.sleep(0.01)

        await wait_for(lambda: engine.open_sid is not None)
        sid = engine.open_sid
        if sid is None:  # pragma: no cover - defensive
            raise RuntimeError("sid not set")

        ctx = adapter._contexts.get(sid)
        if ctx is None:  # pragma: no cover - defensive
            raise RuntimeError("context missing")

        bus.publish(
            {
                "type": "asr.unavailable",
                "sid": sid,
                "reason": "upstream_closed",
                "details": "concurrent_session: concurrent_session_usage",
            }
        )

        await asyncio.sleep(0.05)

        await queue.put({"type": "websocket.receive", "bytes": b"\x00" * 4})
        await asyncio.sleep(0.05)

        snapshot = {
            "recovering_until": ctx.asr_recovering_until,
            "recovering_reason": ctx.asr_recovering_reason,
            "audio_logged": ctx.asr_recovering_audio_logged,
            "vendor": ctx.asr_vendor,
        }

        await queue.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            task.cancel()
            raise

        return sent, snapshot

    def test_binary_pre_ready_rejected(self) -> None:
        sent, engine, audio_events = asyncio.run(self._exercise(publish_ready=False))

        accept_messages = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accept_messages), 1)

        outbound_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text")
        ]
        error_frames = [payload for payload in outbound_payloads if payload.get("type") == "error"]
        self.assertTrue(error_frames)
        for payload in error_frames:
            self.assertEqual(
                payload,
                {"type": "error", "code": "audio_not_expected", "detail": "asr not ready"},
            )

        close_frames = [msg for msg in sent if msg.get("type") == "websocket.close"]
        self.assertEqual(len(close_frames), 1)
        self.assertEqual(close_frames[0].get("code"), 1003)

        self.assertFalse(engine.audio_calls)
        self.assertEqual(len(audio_events), 1)
        meta = audio_events[0]["meta"]
        self.assertEqual(meta["error"], "audio_not_expected")
        self.assertEqual(meta["ws"]["size"], 4)

    def test_binary_post_ready_accepted(self) -> None:
        sent, engine, audio_events = asyncio.run(self._exercise(publish_ready=True))

        accept_messages = [msg for msg in sent if msg.get("type") == "websocket.accept"]
        self.assertEqual(len(accept_messages), 1)

        outbound_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text")
        ]
        error_frames = [payload for payload in outbound_payloads if payload.get("type") == "error"]
        self.assertFalse(error_frames)

        close_frames = [msg for msg in sent if msg.get("type") == "websocket.close"]
        self.assertFalse(close_frames)

        self.assertEqual(len(engine.audio_calls), 1)
        call = engine.audio_calls[0]
        self.assertEqual(call[2], 0)
        self.assertEqual(call[1], b"\x00" * 4)

        self.assertEqual(len(audio_events), 1)
        self.assertNotIn("error", audio_events[0]["meta"])
        self.assertEqual(audio_events[0]["meta"]["seq"], 0)

    @patch.object(ChatV2Adapter, "_allowed_asr_vendors", return_value=["speechmatics"])
    def test_speechmatics_concurrency_sets_grace(self, _mock_allowed: Any) -> None:
        sent, snapshot = asyncio.run(self._exercise_speechmatics_concurrency())

        self.assertEqual(snapshot["vendor"], "speechmatics")
        self.assertEqual(snapshot["recovering_reason"], "concurrent_session")
        self.assertTrue(snapshot["audio_logged"])
        self.assertGreater(snapshot["recovering_until"], time.monotonic())

        outbound_payloads = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text")
        ]
        for payload in outbound_payloads:
            self.assertNotEqual(payload.get("type"), "error")

        close_frames = [msg for msg in sent if msg.get("type") == "websocket.close"]
        for frame in close_frames:
            self.assertNotEqual(frame.get("code"), 1003)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
