import asyncio
import json
from collections import deque

from app.ws import ws_asgi


class _DelayedOpenDeepgram:
    """Deepgram stub that delays open signalling to exercise CloseStream handling."""

    instances = []

    def __init__(self, _cfg=None):
        self.__class__.instances.append(self)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._open_evt: asyncio.Event = asyncio.Event()
        self._is_open = False
        self.close_called = False
        self.closed_before_open = False
        self.sent_chunks = []

    async def connect(self):
        async def _delayed_ready():
            await asyncio.sleep(0.2)
            self._is_open = True
            self._open_evt.set()
            await self._queue.put({"type": "asr_open"})
            await asyncio.sleep(0.05)
            await self._queue.put(None)

        asyncio.create_task(_delayed_ready())

    def is_open(self):
        return self._is_open

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def send(self, chunk: bytes):
        self.sent_chunks.append(bytes(chunk))
        if not self._open_evt.is_set():
            await self._open_evt.wait()

    async def close(self, wait_for_final: bool = True, **_):
        self.close_called = True
        self.closed_before_open = not self._open_evt.is_set()
        await self._queue.put(None)


class _ImmediateFinalDeepgram:
    """Deepgram stub that emits a final result before CloseStream."""

    instances = []

    def __init__(self, _cfg=None):
        self.__class__.instances.append(self)
        self._queue: asyncio.Queue = asyncio.Queue()
        self.close_called = False

    async def connect(self):
        async def _emit_final():
            await asyncio.sleep(0.01)
            await self._queue.put({"type": "asr_open"})
            await asyncio.sleep(0.01)
            await self._queue.put({"type": "user_final", "text": ""})

        asyncio.create_task(_emit_final())

    def is_open(self):
        return True

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def send(self, chunk: bytes):
        return

    async def close(self, wait_for_final: bool = True, **_):
        self.close_called = True
        await self._queue.put(None)


def test_close_stream_waits_for_delayed_open(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")
    monkeypatch.setenv("WS_NO_AUDIO_DETECT_WINDOW_S", "0")
    monkeypatch.setenv("NULL_TURN_MIN_CHARS", "0")
    monkeypatch.setenv("NULL_TURN_MIN_VOICED_MS", "0")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "0")

    admin_events = []

    def _capture_admin(event, **payload):
        admin_events.append((event, payload))

    monkeypatch.setattr(ws_asgi, "_admin_emit", _capture_admin)

    _DelayedOpenDeepgram.instances = []
    monkeypatch.setattr(ws_asgi, "DeepgramClient", _DelayedOpenDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

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
        loop.run_until_complete(asyncio.sleep(0.1))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    instance = _DelayedOpenDeepgram.instances[0]

    # Either we waited for open before closing or we avoided closing altogether.
    assert not instance.closed_before_open, "Deepgram close should not run before the socket opens"

    payloads = [
        json.loads(m["text"])
        for m in sent
        if m.get("type") == "websocket.send" and m.get("text")
    ]

    latency_frames = [p for p in payloads if p.get("type") == "latency_breakdown"]
    assert latency_frames, "latency breakdown frame should be sent to the client"
    latency_payload = latency_frames[0]
    assert latency_payload.get("synthetic") is True
    assert latency_payload.get("ms") is not None
    assert latency_payload.get("reason")

    latency_admin = [
        payload for event, payload in admin_events if event == "latency_breakdown"
    ]
    assert latency_admin, f"expected latency breakdown admin event, saw {admin_events}"
    admin_payload = latency_admin[0]
    assert admin_payload.get("synthetic") is True
    assert admin_payload.get("ms") == latency_payload.get("ms")
    assert admin_payload.get("reason") == latency_payload.get("reason")

    finals = [p for p in payloads if p.get("type") == "Results" and p.get("channel", {}).get("is_final")]

    assert finals, "CloseStream should emit a final result even if provider open is delayed"


def test_close_stream_with_existing_final_does_not_emit_synthetic(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")
    monkeypatch.setenv("WS_NO_AUDIO_DETECT_WINDOW_S", "0")
    monkeypatch.setenv("NULL_TURN_MIN_CHARS", "0")
    monkeypatch.setenv("NULL_TURN_MIN_VOICED_MS", "0")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "0")

    monkeypatch.setattr(ws_asgi, "_admin_emit", lambda *_, **__: None)

    _ImmediateFinalDeepgram.instances = []
    monkeypatch.setattr(ws_asgi, "DeepgramClient", _ImmediateFinalDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent = []

    async def _receive():
        if not events:
            return {"type": "websocket.disconnect"}
        next_event = events[0]
        text = next_event.get("text")
        if text:
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            if payload.get("type") == "CloseStream":
                await asyncio.sleep(0.05)
        return events.popleft()

    async def _send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    payloads = [
        json.loads(m["text"])
        for m in sent
        if m.get("type") == "websocket.send" and m.get("text")
    ]

    finals = [p for p in payloads if p.get("type") == "Results" and p.get("channel", {}).get("is_final")]

    assert len(finals) == 1, "Only provider final should be emitted when already observed"


def test_close_stream_ack_reaches_admin_log(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")
    monkeypatch.setenv("WS_NO_AUDIO_DETECT_WINDOW_S", "0")
    monkeypatch.setenv("NULL_TURN_MIN_CHARS", "0")
    monkeypatch.setenv("NULL_TURN_MIN_VOICED_MS", "0")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "0")

    admin_events = []

    def _capture_admin(event, **payload):
        admin_events.append((event, payload))

    monkeypatch.setattr(ws_asgi, "_admin_emit", _capture_admin)

    class _AdminDiagDeepgram:
        instances = []

        def __init__(self, cfg=None):
            self.__class__.instances.append(self)
            self._cfg = cfg or {}
            self._open_sent = False

        async def connect(self):
            return

        def is_open(self):
            return True

        async def events(self):
            if not self._open_sent:
                self._open_sent = True
                yield {"type": "asr_open"}
            return

        async def send(self, _chunk):
            return

        async def close(self, wait_for_final: bool = True, **_):
            hook = self._cfg.get("_diag_hook")
            assert callable(hook), "Deepgram diag hook should be provided"
            payload = {
                "provider": "deepgram",
                "session_id": self._cfg.get("session_id"),
                "tag": self._cfg.get("_url_tag"),
                "status": "ok",
                "drain_failed": False,
            }
            hook(
                "CloseStream ack",
                **{k: v for k, v in payload.items() if v is not None},
            )

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _AdminDiagDeepgram)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

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
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    ack_events = [
        payload
        for event, payload in admin_events
        if event == "asr:diag" and payload.get("label") == "CloseStream ack"
    ]

    assert ack_events, f"expected CloseStream ack admin event, saw {admin_events}"
    ack_payload = ack_events[0]
    assert ack_payload.get("status") == "ok"
    assert ack_payload.get("session_id") == "default"
