"""Tests for the outbound WebSocket bridge in the chat.v2 adapter."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock
import uuid
from typing import Any, Callable, Dict

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.telemetry import bus
from app.voice_v2 import (
    EVT_ASR_OPEN,
    EVT_CLIENT_MIC_OPEN,
    EVT_HUD_STATE,
    EVT_WS_AUDIO_SEND,
    EVT_WS_JSON_SEND,
)
from app.security.jwt_utils import mint_ws_token
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


class _StubAsrRuntime:
    """ASR runtime stub that records prearm invocations."""

    def __init__(self) -> None:
        self.prearm_calls: list[str] = []

    def on_ws_open(self, sid: str) -> None:  # pragma: no cover - noop for tests
        return

    def on_ws_close(self, sid: str) -> None:  # pragma: no cover - noop for tests
        return

    def on_ws_audio(self, sid: str, chunk: bytes) -> None:  # pragma: no cover - noop
        return

    def prearm(self, sid: str) -> None:
        self.prearm_calls.append(sid)


class OutboundHarness:
    """Helper for driving the adapter within an asyncio test."""

    def __init__(self, adapter: ChatV2Adapter, engine: RecordingEngine) -> None:
        self.adapter = adapter
        self.engine = engine
        self._token_sid = f"sid-{uuid.uuid4().hex}"
        token = mint_ws_token("user-1", self._token_sid, False)
        self.scope = {
            "type": "websocket",
            "subprotocols": [CHAT_V2_SUBPROTOCOL],
            "headers": [(b"authorization", b"Bearer test-token")],
            "client": ("127.0.0.1", 1234),
            "query_string": f"access_token={token}".encode("ascii"),
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
        self.outbound_frames.clear()
        self.binary_frames.clear()

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

    def test_start_listening_handoff_after_tts_end(self) -> None:
        asyncio.run(self._test_start_listening_handoff())

    def test_listen_handoff_waits_for_mask_off(self) -> None:
        asyncio.run(self._test_listen_handoff_waits_for_mask_off())

    def test_audio_frames_forwarded_as_binary_messages(self) -> None:
        asyncio.run(self._test_audio_forwarding())

    def test_mic_open_timeout_triggers_nudge(self) -> None:
        asyncio.run(self._test_mic_open_timeout_nudge())

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

    async def _test_start_listening_handoff(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        runtime = _StubAsrRuntime()
        adapter.asr_runtime = runtime
        harness = OutboundHarness(adapter, engine)
        await harness.start()
        hud_events: list[dict] = []
        mic_events: list[dict] = []
        token = bus.subscribe(EVT_HUD_STATE, hud_events.append)
        mic_token = bus.subscribe(EVT_CLIENT_MIC_OPEN, mic_events.append)
        try:
            sid = harness.sid
            policy_payload = {
                "type": "policy.interaction",
                "interaction_id": "turn-1",
                "actions": ["assistant.say", "assistant.await_user"],
            }
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": policy_payload})
            await asyncio.sleep(0.01)

            tts_end_payload = {"type": "tts.end", "utt_id": "utt-1"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": tts_end_payload})

            await harness.wait_for(lambda: bool(runtime.prearm_calls))
            self.assertEqual(runtime.prearm_calls, [sid])

            bus.publish({"type": EVT_ASR_OPEN, "sid": sid})

            frame = await harness.wait_for_outbound(
                lambda data: data.get("type") == "start_listening"
            )
            self.assertEqual(frame, {"type": "start_listening"})

            ctx = adapter._contexts.get(sid)
            if ctx is not None:
                ctx.asr_ready = True
            await harness._inbound.put({"type": "websocket.receive", "bytes": b"\x00\x00"})

            await harness.wait_for(lambda: bool(mic_events))
            self.assertEqual(mic_events[-1].get("meta", {}).get("state"), "open")

            await harness.wait_for(lambda: bool(hud_events))
            self.assertEqual(hud_events[-1]["meta"].get("state"), "Listening")
        finally:
            bus.unsubscribe(token)
            bus.unsubscribe(mic_token)
            await harness.close()

    async def _test_listen_handoff_waits_for_mask_off(self) -> None:
        engine = RecordingEngine()
        adapter = ChatV2Adapter(engine=engine)
        runtime = _StubAsrRuntime()
        adapter.asr_runtime = runtime
        harness = OutboundHarness(adapter, engine)
        await harness.start()

        sid = harness.sid
        recorded: list[dict] = []
        interesting = {"EVT_TTS_MASK", EVT_HUD_STATE, EVT_CLIENT_MIC_OPEN}

        def _record(event: dict) -> None:
            if event.get("sid") != sid:
                return
            if event.get("type") in interesting:
                recorded.append(dict(event))

        token_order = bus.subscribe("*", _record)
        try:
            policy_payload = {
                "type": "policy.interaction",
                "interaction_id": "turn-1",
                "actions": ["assistant.say", "assistant.await_user"],
            }
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": policy_payload})
            await asyncio.sleep(0.01)

            bus.publish({"type": "EVT_TTS_MASK", "sid": sid, "phase": "engaged"})

            tts_end_payload = {"type": "tts.end", "utt_id": "utt-1"}
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": tts_end_payload})

            await harness.wait_for(lambda: bool(runtime.prearm_calls))
            self.assertEqual(runtime.prearm_calls, [sid])

            bus.publish({"type": EVT_ASR_OPEN, "sid": sid})

            await asyncio.sleep(0.05)
            start_frames = [
                frame for frame in harness.outbound_frames if frame.get("type") == "start_listening"
            ]
            self.assertFalse(start_frames)

            bus.publish({"type": "EVT_TTS_MASK", "sid": sid, "phase": "off"})

            frame = await harness.wait_for_outbound(
                lambda data: data.get("type") == "start_listening"
            )
            self.assertEqual(frame, {"type": "start_listening"})

            ctx = adapter._contexts.get(sid)
            if ctx is not None:
                ctx.asr_ready = True
            await harness._inbound.put({"type": "websocket.receive", "bytes": b"\x01\x02"})

            await harness.wait_for(
                lambda: any(evt.get("type") == EVT_CLIENT_MIC_OPEN for evt in recorded)
            )

            mask_off_index = next(
                idx
                for idx, event in enumerate(recorded)
                if event.get("type") == "EVT_TTS_MASK" and event.get("phase") == "off"
            )
            hud_index = next(
                idx
                for idx, event in enumerate(recorded)
                if event.get("type") == EVT_HUD_STATE
            )
            mic_index = next(
                idx
                for idx, event in enumerate(recorded)
                if event.get("type") == EVT_CLIENT_MIC_OPEN
            )

            hud_events = [evt for evt in recorded if evt.get("type") == EVT_HUD_STATE]
            mic_events = [evt for evt in recorded if evt.get("type") == EVT_CLIENT_MIC_OPEN]

            self.assertLess(mask_off_index, hud_index)
            self.assertLess(hud_index, mic_index)
            self.assertEqual(recorded[hud_index]["meta"].get("state"), "Listening")
            self.assertEqual(recorded[mic_index]["meta"].get("state"), "open")
            self.assertEqual(len(hud_events), 1)
            self.assertEqual(len(mic_events), 1)
        finally:
            bus.unsubscribe(token_order)
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

    async def _test_mic_open_timeout_nudge(self) -> None:
        with mock.patch("app.ws.adapter._MIC_OPEN_TIMEOUT_SECONDS", 0.05):
            engine = RecordingEngine()
            adapter = ChatV2Adapter(engine=engine)
            runtime = _StubAsrRuntime()
            adapter.asr_runtime = runtime
            harness = OutboundHarness(adapter, engine)
            await harness.start()

            sid = harness.sid
            mic_events: list[dict] = []
            token = bus.subscribe(EVT_CLIENT_MIC_OPEN, mic_events.append)
            try:
                policy_payload = {
                    "type": "policy.interaction",
                    "interaction_id": "turn-1",
                    "actions": ["assistant.say", "assistant.await_user"],
                }
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": policy_payload})
                await asyncio.sleep(0.01)

                tts_end_payload = {"type": "tts.end", "utt_id": "utt-1"}
                bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": tts_end_payload})

                await harness.wait_for(lambda: bool(runtime.prearm_calls))
                bus.publish({"type": EVT_ASR_OPEN, "sid": sid})

                frame = await harness.wait_for_outbound(
                    lambda data: data.get("type") == "start_listening"
                )
                self.assertEqual(frame, {"type": "start_listening"})

                nudge_frame = await harness.wait_for_outbound(
                    lambda data: data.get("type") == "hud.nudge"
                )
                self.assertEqual(nudge_frame.get("code"), "mic_permissions")
                self.assertEqual(nudge_frame.get("reason"), "mic_open_timeout")
                self.assertFalse(mic_events)

                ctx = adapter._contexts.get(sid)
                if ctx is not None:
                    ctx.asr_ready = True
                await harness._inbound.put({"type": "websocket.receive", "bytes": b"\x03\x03"})

                await harness.wait_for(lambda: bool(mic_events))
                nudge_frames = [
                    frame for frame in harness.outbound_frames if frame.get("type") == "hud.nudge"
                ]
                self.assertEqual(len(nudge_frames), 1)
            finally:
                bus.unsubscribe(token)
                await harness.close()


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
