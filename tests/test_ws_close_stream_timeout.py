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


def test_close_stream_waits_for_delayed_open(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")

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
        loop.run_until_complete(asyncio.sleep(0.05))
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

    finals = [p for p in payloads if p.get("type") == "Results" and p.get("channel", {}).get("is_final")]

    assert finals, "CloseStream should emit a final result even if provider open is delayed"
