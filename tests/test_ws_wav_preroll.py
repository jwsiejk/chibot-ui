import asyncio
import json
from collections import deque
from typing import Optional

from app.ws import ws_asgi
from app.services.audio import container_sniffer


class _TrackingSniffer(container_sniffer.AudioContainerSniffer):
    instances = []

    def __init__(self):
        super().__init__()
        self.feed_chunks = []
        self.__class__.instances.append(self)

    def feed(self, chunk: bytes):
        self.feed_chunks.append(bytes(chunk))
        return super().feed(chunk)


class _TrackingDeepgram:
    instances = []

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.sent_chunks = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self.__class__.instances.append(self)

    async def connect(self):
        await self._queue.put({"type": "asr_open"})
        await self._queue.put(None)

    def is_open(self):
        return True

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                break
            yield ev

    async def send(self, chunk: bytes):
        self.sent_chunks.append(bytes(chunk))

    async def close(self, wait_for_final: bool = True, **_):
        return


def _run_ws_session(
    monkeypatch,
    webm_chunk: bytes,
    wav_preroll: bytes = b"RIFF1234",
    *,
    audio_start_mime: Optional[str] = None,
):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test")
    monkeypatch.setenv("DG_LINGER_MS", "0")
    monkeypatch.setenv("ASR_FINAL_GRACE_S", "0")

    _TrackingSniffer.instances = []
    _TrackingDeepgram.instances = []

    monkeypatch.setattr(ws_asgi, "AudioContainerSniffer", _TrackingSniffer)
    monkeypatch.setattr(ws_asgi, "DeepgramClient", _TrackingDeepgram)
    monkeypatch.setattr(ws_asgi.bus, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(ws_asgi.bus, "current_assistant_turn", lambda sid: None)
    monkeypatch.setattr(ws_asgi, "_admin_emit", None)
    monkeypatch.setattr(ws_asgi, "_emit_admin_nlu_event", lambda *a, **k: None)

    log_events = []

    def _capture_jlog(event: str, **fields):
        entry = dict(fields)
        entry.setdefault("event", event)
        log_events.append(entry)

    monkeypatch.setattr(ws_asgi, "_jlog", _capture_jlog)

    events_list = [{"type": "websocket.receive", "bytes": wav_preroll}]
    if audio_start_mime is not None:
        events_list.append(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "AudioStart", "mime": audio_start_mime}),
            }
        )
    events_list.extend(
        [
            {"type": "websocket.receive", "bytes": webm_chunk},
            {"type": "websocket.receive", "text": json.dumps({"type": "CloseStream"})},
            {"type": "websocket.disconnect"},
        ]
    )
    events = deque(events_list)

    sent_messages = []

    async def _receive():
        return events.popleft() if events else {"type": "websocket.disconnect"}

    async def _send(msg):
        sent_messages.append(msg)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    sniffer_instance = _TrackingSniffer.instances[0]
    deepgram_instance = _TrackingDeepgram.instances[0]

    return {
        "sniffer": sniffer_instance,
        "deepgram": deepgram_instance,
        "wav_preroll": wav_preroll,
        "webm_chunk": webm_chunk,
        "logs": log_events,
    }


def test_wav_preroll_is_ignored(monkeypatch):
    webm_chunk = b"\x1aE\xdf\xa3OPUSDATA"
    result = _run_ws_session(monkeypatch, webm_chunk)

    sniffer_instance = result["sniffer"]
    deepgram_instance = result["deepgram"]
    wav_preroll = result["wav_preroll"]
    webm_chunk = result["webm_chunk"]

    assert sniffer_instance.feed_chunks == [webm_chunk]
    assert wav_preroll not in deepgram_instance.sent_chunks
    assert webm_chunk in deepgram_instance.sent_chunks

    transport = deepgram_instance.cfg.get("_transport") or {}
    assert transport.get("container") == "webm"
    assert transport.get("codec") == "opus"
    assert transport.get("containerized_opus") is True


def test_webm_chunk_larger_than_window(monkeypatch):
    webm_chunk = container_sniffer.AudioContainerSniffer.EBML_MAGIC + b"X" * (
        container_sniffer.AudioContainerSniffer.MAX_WINDOW + 10
    )

    result = _run_ws_session(monkeypatch, webm_chunk)

    sniffer_instance = result["sniffer"]
    deepgram_instance = result["deepgram"]
    wav_preroll = result["wav_preroll"]
    webm_chunk = result["webm_chunk"]

    assert sniffer_instance.feed_chunks == [webm_chunk]
    assert wav_preroll not in deepgram_instance.sent_chunks
    assert webm_chunk in deepgram_instance.sent_chunks

    transport = deepgram_instance.cfg.get("_transport") or {}
    assert transport.get("container") == "webm"
    assert transport.get("codec") == "opus"
    assert transport.get("containerized_opus") is True


def test_audio_start_hint_controls_connect(monkeypatch):
    webm_chunk = b"\x1aE\xdf\xa3OPUSDATA"
    result = _run_ws_session(
        monkeypatch,
        webm_chunk,
        audio_start_mime="audio/webm; codecs=opus",
    )

    logs = result["logs"]
    hint_index = next(
        i for i, entry in enumerate(logs) if entry.get("event") == "ws_audio_hint"
    )
    schedule_index = next(
        i for i, entry in enumerate(logs) if entry.get("event") == "asr_connect_schedule"
    )

    assert schedule_index > hint_index

    begin_entry = next(
        entry for entry in logs if entry.get("event") == "asr_connect_begin"
    )
    transport = begin_entry.get("transport") or {}
    assert transport.get("containerized_opus") is True
    assert transport.get("container") == "webm"
    assert transport.get("codec") == "opus"


def test_webm_hint_gate_remains_open(monkeypatch):
    webm_chunk = b"\x1aE\xdf\xa3OPUSDATA"
    gate_events = []

    def _observe(open_state: bool, reason: Optional[str]):
        gate_events.append((open_state, reason))

    monkeypatch.setattr(ws_asgi, "CONNECT_GATE_OBSERVER", _observe, raising=False)

    result = _run_ws_session(
        monkeypatch,
        webm_chunk,
        audio_start_mime="audio/webm; codecs=opus",
    )

    assert any(open_state and reason == "audio_start_webm" for open_state, reason in gate_events)

    log_events = [entry.get("event") for entry in result["logs"]]
    assert "asr_connect_schedule" in log_events
    assert "fallback_no_audio" not in log_events
