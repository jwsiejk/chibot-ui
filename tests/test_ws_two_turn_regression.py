import asyncio
import json
from collections import deque

import app.services.streaming_asr.deepgram_client as dg_mod
from app.ws import ws_asgi


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


def _run_ws_session(events):
    sent = []

    async def _receive():
        return events.popleft() if events else {"type": "websocket.disconnect"}

    async def _send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        asyncio.set_event_loop(None)
        loop.close()

    return sent


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
