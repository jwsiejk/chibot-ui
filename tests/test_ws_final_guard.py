import asyncio
import json
from collections import deque

from app.services.streaming_asr import deepgram_client
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


def test_default_guard_waits_for_deepgram_default(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.delenv("WS_FINAL_GUARD_MS", raising=False)

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

    class _DefaultGuardDeepgram:
        instances = []

        def __init__(self, cfg):
            self.cfg = cfg
            self._queue: asyncio.Queue = asyncio.Queue()
            self._final_sent = False
            self.guard_ms = 0
            _DefaultGuardDeepgram.instances.append(self)

        async def connect(self):
            deepgram_client._dg_url(self.cfg)
            self.guard_ms = int(self.cfg.get("_effective_utterance_end_ms") or 0)
            await self._queue.put({"type": "asr_open"})

        async def events(self):
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev

        async def send(self, chunk: bytes):
            if not self._final_sent:
                await self._queue.put({"type": "user_final", "text": "hello default"})
                self._final_sent = True

        async def close(self, wait_for_final=True):
            await self._queue.put(None)

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _DefaultGuardDeepgram)

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

        loop.run_until_complete(asyncio.sleep(1.5))
        interim_payloads = _json_payloads(sent)
        interim_finals = [
            p
            for p in interim_payloads
            if p.get("type") == "Results"
            and (p.get("channel") or {}).get("is_final")
        ]
        assert not interim_finals, "Final results should wait for default guard"

        before_len = len(sent)

        loop.run_until_complete(asyncio.sleep(1.0))
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
    assert _DefaultGuardDeepgram.instances, "Deepgram client should be instantiated"
    assert (
        _DefaultGuardDeepgram.instances[0].guard_ms == 2000
    ), "Default guard should use Deepgram default"


def test_late_audio_after_final_emits_single_pair(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "0")

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

    class _LateAudioDeepgram:
        instances = []

        def __init__(self, cfg):
            self.cfg = cfg
            self._queue: asyncio.Queue = asyncio.Queue()
            self._final_sent = False
            _LateAudioDeepgram.instances.append(self)

        async def connect(self):
            await self._queue.put({"type": "asr_open"})

        async def events(self):
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev

        async def send(self, _chunk: bytes):
            if not self._final_sent:
                self._final_sent = True
                await self._queue.put({"type": "user_final", "text": "turn-one"})

        async def close(self, wait_for_final=True):
            await self._queue.put(None)

    monkeypatch.setattr(ws_asgi, "DeepgramClient", _LateAudioDeepgram)

    sent = []

    async def _send(message):
        sent.append(message)

    events = deque(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x01"},
            {"type": "websocket.receive", "bytes": b"\x02\x03", "_delay": 0.05},
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "CloseStream"}),
                "_delay": 0.05,
            },
            {"type": "websocket.disconnect"},
        ]
    )

    async def _receive():
        if not events:
            return {"type": "websocket.disconnect"}
        ev = events.popleft()
        delay = ev.pop("_delay", 0)
        if delay:
            await asyncio.sleep(delay)
        return ev

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        main_task = loop.create_task(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))
        loop.run_until_complete(main_task)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    payloads = _json_payloads(sent)

    finals_by_turn = {}
    ends_by_turn = {}
    for payload in payloads:
        if payload.get("type") == "Results" and (payload.get("channel") or {}).get(
            "is_final"
        ):
            turn_id = payload.get("turn_id")
            finals_by_turn[turn_id] = finals_by_turn.get(turn_id, 0) + 1
        elif payload.get("type") == "UtteranceEnd":
            turn_id = payload.get("turn_id")
            ends_by_turn[turn_id] = ends_by_turn.get(turn_id, 0) + 1

    assert finals_by_turn, "Expected at least one final result"
    assert finals_by_turn == ends_by_turn
    assert all(count == 1 for count in finals_by_turn.values())
    assert all(count == 1 for count in ends_by_turn.values())
    assert llm_calls, "LLM turn should be invoked for final results"


def test_deepgram_final_without_transcript_still_emits(monkeypatch):
    monkeypatch.setenv("WS_TOKEN_REQUIRED", "0")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("WS_FINAL_GUARD_MS", "0")
    monkeypatch.setenv("DG_KEEPALIVE_INTERVAL_S", "0")

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

    partial_text = "fallback transcript"

    class _ScriptedWS:
        def __init__(self, payloads):
            self._payloads = deque(payloads)
            self.open = True
            self.sent = []

        async def send(self, data):
            self.sent.append(data)

        async def close(self):
            self.open = False

        def __aiter__(self):
            ws = self

            async def _gen():
                while ws._payloads:
                    await asyncio.sleep(0)
                    yield ws._payloads.popleft()
                ws.open = False

            return _gen()

    scripted_messages = [
        json.dumps(
            {
                "type": "Results",
                "channel": {
                    "alternatives": [{"transcript": partial_text}],
                    "is_final": False,
                },
            }
        ),
        json.dumps(
            {
                "type": "Results",
                "channel": {
                    "alternatives": [],
                    "is_final": True,
                },
            }
        ),
    ]

    ws_instances = []

    async def fake_ws_connect(*args, **kwargs):
        ws = _ScriptedWS(list(scripted_messages))
        ws_instances.append(ws)
        return ws

    monkeypatch.setattr(deepgram_client.websockets, "connect", fake_ws_connect)

    dg_instances = []

    def tracked_deepgram(cfg):
        cfg["utterance_end_ms"] = 0
        cfg["_effective_utterance_end_ms"] = 0
        client = deepgram_client.DeepgramClient(cfg)
        dg_instances.append(client)
        return client

    monkeypatch.setattr(ws_asgi, "DeepgramClient", tracked_deepgram)

    sent = []

    async def _send(message):
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws/v1/chat", "query_string": b""}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        events: asyncio.Queue = asyncio.Queue()
        events.put_nowait({"type": "websocket.receive", "bytes": b"\x00\x01"})
        events.put_nowait({"type": "websocket.receive", "bytes": b"\x02\x03"})

        async def _receive():
            ev = await events.get()
            delay = ev.pop("_delay", 0)
            if delay:
                await asyncio.sleep(delay)
            return ev

        main_task = loop.create_task(ws_asgi._ws_chat_asgi_impl(scope, _receive, _send))

        finals = []
        payloads = []
        for _ in range(10):
            loop.run_until_complete(asyncio.sleep(0.05))
            payloads = _json_payloads(sent)
            finals = [
                p
                for p in payloads
                if p.get("type") == "Results"
                and (p.get("channel") or {}).get("is_final")
            ]
            if finals:
                break
        assert finals, "Final Results frame should be emitted despite empty transcript"
        assert finals[0].get("channel", {}).get("alternatives", [{}])[0].get(
            "transcript"
        ) == partial_text
        assert any(p.get("type") == "UtteranceEnd" for p in payloads), (
            "UtteranceEnd should follow final Results"
        )

        loop.run_until_complete(events.put({"type": "websocket.disconnect"}))
        loop.run_until_complete(main_task)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    assert ws_instances, "Fake websocket should have been used"
    assert llm_calls, "LLM turn should run after Deepgram final"
    assert dg_instances, "Deepgram client should be instantiated"
    assert not dg_instances[0]._last_transcript, "Cached transcript should reset after final"
