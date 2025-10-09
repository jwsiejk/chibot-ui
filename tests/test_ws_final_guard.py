import asyncio
import json

from app.ws import ws_asgi


def _json_payloads(messages):
    out = []
    for msg in messages:
        if msg.get("type") != "websocket.send":
            continue
        text = msg.get("text")
        if not text:
            continue
        try:
            out.append(json.loads(text))
        except Exception:
            continue
    return out


def test_provider_final_waits_for_guard_window(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "200")

    monkeypatch.setattr(ws_asgi.bus, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(ws_asgi, "_admin_emit", None)
    monkeypatch.setattr(ws_asgi, "_emit_admin_nlu_event", lambda *a, **k: None)

    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(ws_asgi.asyncio, "to_thread", immediate_to_thread)

    def fake_prepare(text, meta=None, **kwargs):
        return {}, {}, {}

    monkeypatch.setattr(ws_asgi, "prepare_turn_metadata", fake_prepare)

    llm_calls = []

    def fake_run_ws_user_turn(session_id, text, corr_id=None, **kwargs):
        llm_calls.append((session_id, text))

    monkeypatch.setattr(ws_asgi, "run_ws_user_turn", fake_run_ws_user_turn)

    class _GuardedFinalDeepgram:
        instances = []

        def __init__(self, cfg):
            self.cfg = cfg
            self._queue: asyncio.Queue = asyncio.Queue()
            self._final_sent = False
            _GuardedFinalDeepgram.instances.append(self)

        async def connect(self):
            await self._queue.put({"type": "asr_open"})

        async def events(self):
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev

        async def send(self, chunk: bytes):
            if not self._final_sent:
                await self._queue.put({"type": "user_final", "text": "hello there"})
                self._final_sent = True

        async def close(self, wait_for_final=True):
            await self._queue.put(None)

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _GuardedFinalDeepgram)

    sent = []

    async def _send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        events: asyncio.Queue = asyncio.Queue()
        events.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x01"})
        events.put_nowait(
            {"type": "websocket.receive", "bytes": b"\x02\x03", "_delay": 0.05}
        )

        async def _receive():
            ev = await events.get()
            delay = ev.pop("_delay", 0)
            if delay:
                await asyncio.sleep(delay)
            return ev

        main_task = loop.create_task(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))

        loop.run_until_complete(asyncio.sleep(0.1))
        before_len = len(sent)
        payloads_before = _json_payloads(sent)
        finals_before = [
            p
            for p in payloads_before
            if p.get("type") == "Results"
            and (p.get("channel") or {}).get("is_final")
        ]
        assert not finals_before, "Final results should wait for guard window"
        assert not any(p.get("type") == "UtteranceEnd" for p in payloads_before)

        loop.run_until_complete(asyncio.sleep(0.3))
        new_payloads = _json_payloads(sent[before_len:])
        finals_after = [
            p
            for p in new_payloads
            if p.get("type") == "Results"
            and (p.get("channel") or {}).get("is_final")
        ]
        utter_after = [p for p in new_payloads if p.get("type") == "UtteranceEnd"]
        assert len(finals_after) == 1, "Provider final should emit once after guard"
        assert len(utter_after) == 1, "UtteranceEnd should follow provider final"

        loop.run_until_complete(events.put({"type": "websocket.disconnect"}))
        loop.run_until_complete(main_task)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert llm_calls, "LLM turn should still run after guarded final"
