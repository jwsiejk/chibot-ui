import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app.ws import adapter as adapter_module
from app.ws.adapter import AdapterContext, ChatV2Adapter
from app.voice_v2 import EVT_ASR_READY, EVT_TTS_END, EVT_TTS_MASK, EVT_WS_JSON_SEND


class FakeBus:
    def __init__(self) -> None:
        self._counter = 0
        self._subscriptions: Dict[str, Tuple[str, Callable[[dict], None]]] = {}
        self._by_event: Dict[str, List[str]] = {}
        self.published: List[dict] = []

    def subscribe(self, event_type: str, callback: Callable[[dict], None]) -> str:
        token = f"token-{self._counter}";
        self._counter += 1
        self._subscriptions[token] = (event_type, callback)
        self._by_event.setdefault(event_type, []).append(token)
        return token

    def unsubscribe(self, token: str) -> None:
        event_type, _ = self._subscriptions.pop(token, (None, None))
        if not event_type:
            return
        tokens = self._by_event.get(event_type)
        if tokens and token in tokens:
            tokens.remove(token)
            if not tokens:
                self._by_event.pop(event_type, None)

    def publish(self, event: dict) -> None:
        self.published.append(event)
        event_type = event.get("type")
        tokens = list(self._by_event.get(event_type, ()))
        tokens += list(self._by_event.get("*", ()))
        for token in tokens:
            _, callback = self._subscriptions.get(token, (None, None))
            if callback:
                callback(event)


class FakeASRRuntime:
    def __init__(self, bus: FakeBus) -> None:
        self.bus = bus
        self.prearm_calls: List[str] = []
        self.open_calls: List[Tuple[str, str]] = []

    def prearm(self, sid: str, *, keep_warm_ms: int | None = None) -> None:
        self.prearm_calls.append(sid)

    async def open_if_needed(self, sid: str, *, req_id: str) -> None:
        self.open_calls.append((sid, req_id))


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: List[dict] = []
        self.text_frames: List[Dict[str, Any]] = []
        self.binary_frames: List[bytes] = []

    async def send(self, message: dict) -> None:
        self.sent.append(message)
        if message.get("type") == "websocket.send":
            payload = message.get("text")
            if payload is not None:
                self.text_frames.append(json.loads(payload))
            chunk = message.get("bytes")
            if chunk is not None:
                self.binary_frames.append(chunk)

    def frames_of_type(self, frame_type: str) -> List[Dict[str, Any]]:
        return [frame for frame in self.text_frames if frame.get("type") == frame_type]


async def wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


class ListenHarness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bus = FakeBus()
        monkeypatch.setattr(adapter_module, "bus", self.bus)
        self.adapter = ChatV2Adapter()
        self.runtime = FakeASRRuntime(self.bus)
        self.adapter.asr_runtime = self.runtime
        self.ctx = AdapterContext(sid="S1", headers={})
        self.ws = FakeWebSocket()
        self._started = False
        self._last_req_id: Optional[str] = None

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("harness already started")
        self._started = True
        self.adapter._start_asr_ready_tracker(self.ctx)
        self.adapter._start_outbound_bridge(self.ctx, self.ws.send)

    async def stop(self) -> None:
        if not self._started:
            return
        await self.adapter._cleanup_outbound(self.ctx)
        self.adapter._stop_asr_ready_tracker(self.ctx)
        self._started = False

    async def expect_policy(self, req_id: str) -> None:
        payload = {
            "type": "policy.interaction",
            "actions": ["assistant.await_user"],
            "req_id": req_id,
        }
        self.bus.publish({"type": EVT_WS_JSON_SEND, "sid": self.ctx.sid, "payload": payload})
        await wait_for(lambda: self.ctx.await_user_expected and self.ctx.await_user_req_id == req_id)

    async def emit_tts_end(self, req_id: str) -> None:
        frame = {"type": "tts.end", "req_id": req_id}
        self.bus.publish({"type": EVT_WS_JSON_SEND, "sid": self.ctx.sid, "payload": frame})
        self.bus.publish({"type": EVT_TTS_END, "sid": self.ctx.sid, "req_id": req_id})
        await asyncio.sleep(0)
        self._last_req_id = req_id

    async def set_mask(self, phase: str) -> None:
        self.bus.publish({"type": EVT_TTS_MASK, "sid": self.ctx.sid, "phase": phase})
        await wait_for(lambda: self.ctx.tts_mask_phase == phase)

    async def publish_asr_ready(self, req_id: Optional[str] = None) -> None:
        payload = {"type": EVT_ASR_READY, "sid": self.ctx.sid}
        if req_id is not None:
            payload["req_id"] = req_id
        self.bus.publish(payload)
        await wait_for(lambda: self.ctx.asr_ready)

    async def wait_for_listen_task(self) -> None:
        await wait_for(lambda: self.ctx.listen_handoff_task is None)

    @property
    def last_req_id(self) -> Optional[str]:
        return self._last_req_id


def test_listen_handoff_basic_success(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        harness = ListenHarness(monkeypatch)
        await harness.start()
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()
            await wait_for(lambda: len(harness.ws.frames_of_type("asr.ready")) == 1)
            await wait_for(lambda: len(harness.ws.frames_of_type("input.start")) == 1)

            frames = [
                frame
                for frame in harness.ws.text_frames
                if frame.get("type") in {"asr.ready", "input.start"}
            ]
            assert [frame.get("type") for frame in frames] == ["asr.ready", "input.start"]
            assert frames[0]["input"]["codec"] == "pcm_s16le"
            assert frames[1]["capture"]["timeslice_ms"] == 250
            assert any(
                "evt=listen_handoff_ready" in record.message and "req_id=r1" in record.message
                for record in caplog.records
            )
        finally:
            await harness.stop()

    asyncio.run(_run())


def test_listen_handoff_duplicate_triggers_collapse(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        harness = ListenHarness(monkeypatch)
        await harness.start()
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()
            await wait_for(lambda: len(harness.ws.frames_of_type("asr.ready")) == 1)
            await wait_for(lambda: len(harness.ws.frames_of_type("input.start")) == 1)

            initial = [
                frame
                for frame in harness.ws.text_frames
                if frame.get("type") in {"asr.ready", "input.start"}
            ]
            assert len(initial) == 2

            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await asyncio.sleep(0.05)

            frames = [
                frame
                for frame in harness.ws.text_frames
                if frame.get("type") in {"asr.ready", "input.start"}
            ]
            assert len(frames) == 2
            assert any(
                "evt=listen_handoff_skip" in record.message and "req_id=r1" in record.message
                for record in caplog.records
            )
        finally:
            await harness.stop()

    asyncio.run(_run())


def test_listen_handoff_new_turn_resets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _run() -> None:
        harness = ListenHarness(monkeypatch)
        await harness.start()
        caplog.set_level(logging.INFO)
        try:
            await harness.expect_policy("r1")
            await harness.emit_tts_end("r1")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r1") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r1")
            await harness.wait_for_listen_task()
            await wait_for(lambda: len(harness.ws.frames_of_type("input.start")) == 1)

            await harness.expect_policy("r2")
            await harness.emit_tts_end("r2")
            await harness.set_mask("off")
            await wait_for(lambda: ("S1", "r2") in harness.runtime.open_calls)
            await harness.publish_asr_ready("r2")
            await harness.wait_for_listen_task()
            await wait_for(lambda: len(harness.ws.frames_of_type("input.start")) == 2)

            frames = [
                frame
                for frame in harness.ws.text_frames
                if frame.get("type") in {"asr.ready", "input.start"}
            ]
            assert [frame.get("type") for frame in frames] == [
                "asr.ready",
                "input.start",
                "asr.ready",
                "input.start",
            ]
            assert harness.runtime.open_calls == [("S1", "r1"), ("S1", "r2")]
            assert any(
                "evt=listen_handoff_ready" in record.message and "req_id=r2" in record.message
                for record in caplog.records
            )
        finally:
            await harness.stop()

    asyncio.run(_run())
