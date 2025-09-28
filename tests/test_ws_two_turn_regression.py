import asyncio
import json
import time
from collections import deque

import app.services.streaming_asr.deepgram_client as dg_mod
from app.services.streaming import schedule_frames
from app.ws import ws_asgi
from app.ws.bus import bus


class _FakeDeepgram:
    """Minimal Deepgram stub for tests with controllable finals."""

    def __init__(self, _cfg=None):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._final_sent = False
        self._close_calls = 0

    async def connect(self):
        # Mirror provider open event to satisfy the relay loop.
        await self._queue.put({"type": "asr_open"})

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def send(self, _chunk: bytes):
        # First turn: emit a final result immediately.
        if not self._final_sent:
            self._final_sent = True
            await self._queue.put({"type": "user_final", "text": "turn-one"})

    async def close(self, wait_for_final: bool = True, **_):
        self._close_calls += 1
        if not wait_for_final:
            # Signal the relay loop to exit during cleanup.
            await self._queue.put(None)


def _run_ws_session(events, sid: str = "default"):
    sent = []

    async def _receive():
        while True:
            if not events:
                return {"type": "websocket.disconnect"}
            ev = events.popleft()
            if isinstance(ev, dict) and "sleep" in ev:
                await asyncio.sleep(ev["sleep"])
                continue
            return ev

    async def _send(msg):
        sent.append(msg)

    scope = {
        "type": "websocket",
        "path": "/ws/v1/chat",
        "query_string": f"session_id={sid}".encode(),
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        asyncio.set_event_loop(None)
        loop.close()

    return sent


def test_schedule_frames_tracks_current_assistant_turn(monkeypatch):
    sid = "s-schedule-track"
    frames = [
        {"type": "assistant_chunk", "turn_id": "turn-42", "text": "hi"},
        {"type": "assistant_end", "turn_id": "turn-42"},
    ]
    calls = []
    orig_note = bus.note_assistant_turn

    def _capture_note(sid_arg, tid_arg):
        calls.append((sid_arg, tid_arg))
        orig_note(sid_arg, tid_arg)

    monkeypatch.setattr(bus, "note_assistant_turn", _capture_note)

    q = bus.subscribe(sid)
    try:
        schedule_frames(sid, frames)
        q.get(timeout=1.0)
        q.get(timeout=1.0)
    finally:
        bus.unsubscribe(sid, q)
        bus.note_assistant_turn(sid, None)
        bus._canceled.discard((sid, "turn-42"))

    assert (sid, "turn-42") in calls
    assert (sid, None) in calls


def test_ws_barge_commit_cancels_turn(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    sid = "s-barge-commit"
    bus.note_assistant_turn(sid, "turn-commit")

    cancel_calls = []
    orig_cancel = bus.cancel_turn

    def _capture_cancel(sid_arg, tid_arg):
        cancel_calls.append((sid_arg, tid_arg))
        orig_cancel(sid_arg, tid_arg)

    monkeypatch.setattr(bus, "cancel_turn", _capture_cancel)

    start_results = []
    orig_start = ws_asgi.BargeState.start

    def _capture_start(self, *args, **kwargs):
        result = orig_start(self, *args, **kwargs)
        start_results.append(result)
        return result

    monkeypatch.setattr(ws_asgi.BargeState, "start", _capture_start)

    orig_current = bus.current_assistant_turn

    def _patched_current(sid_arg):
        result = orig_current(sid_arg)
        if result:
            return result
        return "turn-commit" if sid_arg == sid else None

    monkeypatch.setattr(bus, "current_assistant_turn", _patched_current)

    assert bus.current_assistant_turn(sid) == "turn-commit"

    events = deque(
        [
            {"type": "websocket.receive", "text": json.dumps({"type": "Configure", "confirm_ms": 30})},
            {"type": "websocket.receive", "bytes": b"\x01"},
            {"sleep": 0.08},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent = _run_ws_session(events, sid=sid)
    time.sleep(0.1)

    state_frames = []
    for msg in sent:
        if msg.get("type") == "websocket.send" and msg.get("text"):
            payload = json.loads(msg["text"])
            if payload.get("type") == "state":
                state_frames.append(payload)

    assert start_results, "barge.start should be invoked"
    phases = [frame.get("phase") for frame in state_frames]
    assert "paused" in phases
    assert "ready" in phases
    assert cancel_calls and cancel_calls[0] == (sid, "turn-commit")
    assert orig_current(sid) is None

    bus.note_assistant_turn(sid, None)
    bus._canceled.discard((sid, "turn-commit"))


def test_ws_close_stream_resumes_without_interrupt(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    sid = "s-barge-cancel"
    bus.note_assistant_turn(sid, "turn-cancel")

    cancel_calls = []
    orig_cancel = bus.cancel_turn

    def _capture_cancel(sid_arg, tid_arg):
        cancel_calls.append((sid_arg, tid_arg))
        orig_cancel(sid_arg, tid_arg)

    monkeypatch.setattr(bus, "cancel_turn", _capture_cancel)

    orig_current = bus.current_assistant_turn

    def _patched_current(sid_arg):
        result = orig_current(sid_arg)
        if result:
            return result
        return "turn-cancel" if sid_arg == sid else None

    monkeypatch.setattr(bus, "current_assistant_turn", _patched_current)

    start_results = []
    orig_start = ws_asgi.BargeState.start

    def _capture_start(self, *args, **kwargs):
        result = orig_start(self, *args, **kwargs)
        start_results.append(result)
        return result

    monkeypatch.setattr(ws_asgi.BargeState, "start", _capture_start)

    assert bus.current_assistant_turn(sid) == "turn-cancel"

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x02"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent = _run_ws_session(events, sid=sid)
    time.sleep(0.05)

    state_frames = []
    for msg in sent:
        if msg.get("type") == "websocket.send" and msg.get("text"):
            payload = json.loads(msg["text"])
            if payload.get("type") == "state":
                state_frames.append(payload)

    phases = [frame.get("phase") for frame in state_frames]
    assert "paused" in phases
    assert "assistant_speaking" in phases
    assert not cancel_calls

    bus.note_assistant_turn(sid, None)
    bus._canceled.discard((sid, "turn-cancel"))
    assert start_results, "barge.start should be invoked"


def test_second_turn_without_final_emits_fallback(monkeypatch):
    # Disable auth and enable the Deepgram path.
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _FakeDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.receive", "bytes": b"\x00\x02"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent = _run_ws_session(events)

    payloads = [
        json.loads(m["text"])
        for m in sent
        if m.get("type") == "websocket.send" and m.get("text")
    ]

    # First turn should surface a provider final.
    turn1 = [p for p in payloads if p.get("turn_id") == 1]
    assert any(p.get("type") == "Results" and p["channel"]["is_final"] for p in turn1)

    # Second turn has no provider final; the server must synthesize the fallback.
    turn2_results = [
        p for p in payloads if p.get("turn_id") == 2 and p.get("type") == "Results"
    ]
    turn2_end = [
        p for p in payloads if p.get("turn_id") == 2 and p.get("type") == "UtteranceEnd"
    ]

    assert turn2_results, "Missing fallback Results for second turn"
    assert turn2_end, "Missing fallback UtteranceEnd for second turn"
    assert turn2_results[-1]["channel"]["is_final"] is True


def test_audio_chunk_retries_until_asr_open(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("DG_OPEN_WAIT_S", "0.01")

    class _RetryDeepgram:
        instances = []

        def __init__(self, _cfg=None):
            self.__class__.instances.append(self)
            self._queue: asyncio.Queue = asyncio.Queue()
            self._ready = asyncio.Event()
            self.received = []

        async def connect(self):
            async def _delayed_open():
                await asyncio.sleep(0.05)
                self._ready.set()
                await self._queue.put({"type": "asr_open"})

            asyncio.create_task(_delayed_open())

        async def events(self):
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev

        async def send(self, chunk: bytes):
            if not self._ready.is_set():
                raise RuntimeError("deepgram_not_connected")
            self.received.append(chunk)

        async def close(self, wait_for_final: bool = True, **_):
            await self._queue.put(None)

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _RetryDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    _run_ws_session(events)

    instance = _RetryDeepgram.instances[0]
    assert instance.received == [b"\x00\x01"]


def test_close_after_first_chunk_does_not_mark_transport_failure(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("DG_OPEN_WAIT_S", "0.02")
    monkeypatch.setenv("DG_CLOSE_DRAIN_TIMEOUT_S", "0.3")
    monkeypatch.setenv("DG_CLOSE_FLUSH_BUDGET_S", "0.5")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0.05")
    monkeypatch.setenv("DG_FINAL_WAIT_S", "0.1")

    logs = []

    def capture(event, **fields):
        logs.append((event, fields))

    monkeypatch.setattr(ws_asgi, "_jlog", capture)

    class _DelayedOpenWS:
        def __init__(self):
            self.open = False
            self.closed = False
            self.closing = False
            self.sent = []

        async def send(self, data):
            self.sent.append(data)

        async def close(self):
            self.open = False
            self.closing = True
            self.closed = True

    class _DelayedDrainDeepgram(dg_mod.DeepgramClient):
        instances = []

        def __init__(self, cfg=None):
            super().__init__(cfg)
            self.__class__.instances.append(self)
            self._ws = _DelayedOpenWS()

        async def connect(self):
            ws = self._ws

            async def _delayed_ready():
                await asyncio.sleep(0.05)
                ws.open = True
                await self._signal_ready()

            asyncio.create_task(_delayed_ready())

        async def _stop_keepalive(self):
            # Override to avoid awaiting non-started keepalive in tests.
            self._keepalive_task = None

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _DelayedDrainDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\xAA" * 96},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent = _run_ws_session(events)

    payloads = [
        json.loads(msg["text"])
        for msg in sent
        if msg.get("type") == "websocket.send" and msg.get("text")
    ]

    errors = [p for p in payloads if p.get("type") == "Error" and p.get("code") == "asr_error"]
    finals = [p for p in payloads if p.get("type") == "Results" and p.get("channel", {}).get("is_final")]
    utterance = [p for p in payloads if p.get("type") == "UtteranceEnd"]

    assert not errors, f"unexpected asr_error payloads: {errors}"
    assert finals, "expected synthetic final result when close races drain"
    assert utterance, "expected UtteranceEnd payload when close races drain"

    log_events = [evt for evt, _ in logs]
    assert "dg_writer_drop" not in log_events
