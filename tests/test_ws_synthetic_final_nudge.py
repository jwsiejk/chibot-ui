import asyncio
import json
from collections import deque

import app.services.streaming as streaming
from app.ws import ws_asgi


class _RecordingDeepgram:
    instances = []

    def __init__(self, cfg=None):
        self.sent_chunks = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._open = False
        self.__class__.instances.append(self)

    async def connect(self):
        self._open = True
        await self._queue.put({"type": "asr_open"})

    def is_open(self):
        return self._open

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def send(self, chunk: bytes):
        self.sent_chunks.append(bytes(chunk))

    async def close(self, wait_for_final: bool = True, **_):
        self._open = False
        await self._queue.put(None)


def test_synthetic_final_triggers_empty_nudge(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")

    _RecordingDeepgram.instances = []

    fallback_calls = []
    fallback_text = "Sorry, I didn’t catch that. Could you say that again?"

    def fake_make_assistant_frames(seed_text, session_id, meta=None, **kwargs):
        frames = [
            {"type": "assistant_chunk", "turn_id": "assist-turn", "text": seed_text},
            {"type": "assistant_end", "turn_id": "assist-turn"},
        ]
        fallback_calls.append(
            {
                "seed_text": seed_text,
                "meta": dict(meta or {}),
                "kwargs": dict(kwargs),
            }
        )
        if kwargs.get("broadcast_immediately", True):
            for frame in frames:
                ws_asgi.bus.broadcast(session_id, frame)
        return "assist-turn", frames

    monkeypatch.setattr(streaming, "make_assistant_frames", fake_make_assistant_frames)
    monkeypatch.setattr(ws_asgi, "DeepgramClient", _RecordingDeepgram)
    monkeypatch.setattr(ws_asgi, "_admin_emit", None)
    monkeypatch.setattr(ws_asgi, "_emit_admin_nlu_event", lambda *a, **k: None)

    log_events = []

    def _capture_jlog(event: str, **fields):
        entry = dict(fields)
        entry.setdefault("event", event)
        log_events.append(entry)

    monkeypatch.setattr(ws_asgi, "_jlog", _capture_jlog)

    sid = "s-synthetic-empty"
    audio_chunk = b"\x01" * 320

    events = deque(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "AudioStart", "mime": "audio/wav"}),
            },
            {"type": "websocket.receive", "bytes": audio_chunk},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )

    sent_messages = []

    async def _receive():
        return events.popleft() if events else {"type": "websocket.disconnect"}

    async def _send(msg):
        sent_messages.append(msg)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": f"session_id={sid}".encode()}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert _RecordingDeepgram.instances, "Deepgram client should be instantiated"
    dg_client = _RecordingDeepgram.instances[0]
    assert dg_client.sent_chunks and len(dg_client.sent_chunks[0]) == len(audio_chunk)

    payloads = [
        json.loads(msg["text"])
        for msg in sent_messages
        if msg.get("type") == "websocket.send" and msg.get("text")
    ]
    result_payload = next(p for p in payloads if p.get("type") == "Results")
    assert result_payload["channel"]["alternatives"][0]["transcript"] == ""
    assert any(p.get("type") == "UtteranceEnd" for p in payloads)

    assert any(call["seed_text"] == fallback_text for call in fallback_calls)
    nudge_log = next(entry for entry in log_events if entry.get("event") == "empty_final_nudge")
    assert nudge_log.get("source") == "synthetic_final"
    assert nudge_log.get("bytes_forwarded", 0) > 0

