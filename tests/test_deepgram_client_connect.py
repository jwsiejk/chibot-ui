import asyncio
import json
import logging
from collections import deque
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.streaming_asr import deepgram_client as dg_mod


class DummyWS:
    def __init__(self):
        self.open = True
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.open = False

    def __aiter__(self):
        async def _gen():
            yield json.dumps({"type": "Metadata"})
        return _gen()


class ConcurrentGuardWS(DummyWS):
    """Websocket double that raises when concurrent sends overlap."""

    def __init__(self):
        super().__init__()
        self._inflight = False
        self._blocked_sends: asyncio.Queue = asyncio.Queue()
        self._auto_release = True

    def hold_next_send(self) -> None:
        """Ensure the next send blocks until released via `wait_for_blocked_send`."""

        self._auto_release = False

    async def wait_for_blocked_send(self):
        """Return (payload, event) for the next blocked send."""

        payload, event = await self._blocked_sends.get()
        return payload, event

    async def send(self, data):
        if self._inflight:
            raise RuntimeError("cannot call send while another coroutine is already waiting")

        self._inflight = True
        try:
            if self._auto_release:
                await asyncio.sleep(0)
                self.sent.append(data)
                return

            self._auto_release = True
            release = asyncio.Event()
            await self._blocked_sends.put((data, release))
            await release.wait()
            self.sent.append(data)
        finally:
            self._inflight = False


def test_connect_prefers_additional_headers(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    record = {}

    async def fake_connect(url, **kwargs):
        record["url"] = url
        record["kwargs"] = kwargs
        return DummyWS()

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()

        assert "additional_headers" in record["kwargs"]
        assert record["kwargs"]["additional_headers"] == [("Authorization", "Token abc123")]
        assert "extra_headers" not in record["kwargs"]
        assert record["kwargs"]["max_size"] is None

        assert json.loads(client._ws.sent[0])["type"] == "Configure"

        event = await asyncio.wait_for(client._ev_queue.get(), timeout=0.1)
        assert event["type"] == "asr_open"

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_flush_keeps_small_chunk_for_containerized_transport(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    async def run():
        client = dg_mod.DeepgramClient({"_transport": {"containerized_opus": True}})
        dummy_ws = DummyWS()
        client._ws = dummy_ws
        client._open_evt.set()
        client._tx_queue.append(b"x" * 32)

        await client._flush_tx()

        assert dummy_ws.sent == [b"x" * 32]
        assert client._tx_queue == deque()

    asyncio.run(run())


def test_connect_falls_back_to_extra_headers(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    calls = []

    async def fake_connect(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert "additional_headers" in kwargs
            raise TypeError("no additional_headers")
        assert "extra_headers" in kwargs
        return DummyWS()

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()

        assert len(calls) == 2
        assert calls[0]["additional_headers"] == [("Authorization", "Token abc123")]
        assert calls[1]["extra_headers"] == [("Authorization", "Token abc123")]
        assert calls[1]["max_size"] is None
        assert json.loads(client._ws.sent[0])["type"] == "Configure"

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_testmode_emits_asr_open_once(monkeypatch):
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", True)

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()

        event = await asyncio.wait_for(client._ev_queue.get(), timeout=0.05)
        assert event["type"] == "asr_open"
        assert client._asr_open_emitted is True

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client._ev_queue.get(), timeout=0.02)

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_keepalive_loop_sends_frames(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setenv("DG_KEEPALIVE_INTERVAL_S", "0.01")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    class KeepaliveWS(DummyWS):
        def __aiter__(self):
            async def _gen():
                yield json.dumps({"type": "Metadata"})
                await asyncio.sleep(0.1)

            return _gen()

    keepalive_ws = KeepaliveWS()

    async def fake_connect(url, **kwargs):
        return keepalive_ws

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()
        await asyncio.sleep(0.05)

        sent_types = [
            json.loads(item).get("type")
            for item in keepalive_ws.sent
            if isinstance(item, str)
        ]

        assert sent_types[0] == "Configure"
        assert any(t == "KeepAlive" for t in sent_types[1:])

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_close_waits_for_final_keepalive(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setenv("DG_KEEPALIVE_INTERVAL_S", "0.01")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    class CloseWaitWS(DummyWS):
        def __aiter__(self):
            async def _gen():
                yield json.dumps({"type": "Metadata"})
                await asyncio.sleep(1)

            return _gen()

    keepalive_ws = CloseWaitWS()

    async def fake_connect(url, **kwargs):
        return keepalive_ws

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    def count_keepalives() -> int:
        return sum(
            1
            for item in keepalive_ws.sent
            if isinstance(item, str) and json.loads(item).get("type") == "KeepAlive"
        )

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()
        client._linger_ms = 0

        await asyncio.sleep(0.05)
        pre_close_keepalives = count_keepalives()

        close_task = asyncio.create_task(client.close(wait_for_final=True, timeout=0.2))

        await asyncio.sleep(0.05)
        assert not close_task.done()

        mid_close_keepalives = count_keepalives()
        assert mid_close_keepalives > pre_close_keepalives

        client._final_event.set()

        await close_task

        post_close_keepalives = count_keepalives()

        await asyncio.sleep(0.05)

        assert count_keepalives() == post_close_keepalives
        assert keepalive_ws.open is False
        assert client._keepalive_task is None

    asyncio.run(run())


def _count_keepalives(ws: DummyWS) -> int:
    return sum(
        1
        for item in ws.sent
        if isinstance(item, str) and json.loads(item).get("type") == "KeepAlive"
    )


def test_keepalive_waits_for_flush_and_close(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setenv("DG_KEEPALIVE_INTERVAL_S", "0.01")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    guard_ws = ConcurrentGuardWS()

    async def fake_connect(url, **kwargs):
        return guard_ws

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()
        await client.connect()
        client._min_valid_bytes = 1

        assert client._keepalive_task and not client._keepalive_task.done()

        async def wait_for_keepalives(target: int) -> int:
            deadline = asyncio.get_running_loop().time() + 0.5
            while _count_keepalives(guard_ws) < target:
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("expected keepalive count to reach target")
                await asyncio.sleep(0.01)
            return _count_keepalives(guard_ws)

        baseline_keepalives = await wait_for_keepalives(1)

        guard_ws.hold_next_send()
        send_task = asyncio.create_task(client.send(b"a" * 4))

        while True:
            payload, release = await asyncio.wait_for(guard_ws.wait_for_blocked_send(), timeout=0.2)
            if payload == b"a" * 4:
                break
            release.set()
            guard_ws.hold_next_send()

        await asyncio.sleep(0.05)
        assert client._keepalive_task and not client._keepalive_task.done()

        release.set()
        await asyncio.wait_for(send_task, timeout=0.2)

        post_flush_keepalives = await wait_for_keepalives(baseline_keepalives + 1)

        guard_ws.hold_next_send()
        close_task = asyncio.create_task(client.close(wait_for_final=True, timeout=0.5))

        while True:
            payload, release = await asyncio.wait_for(guard_ws.wait_for_blocked_send(), timeout=0.2)
            if isinstance(payload, str) and json.loads(payload).get("type") == "CloseStream":
                break
            release.set()
            guard_ws.hold_next_send()

        await asyncio.sleep(0.05)
        assert client._keepalive_task and not client._keepalive_task.done()

        mid_close_keepalives = await wait_for_keepalives(post_flush_keepalives + 1)

        release.set()

        await asyncio.sleep(0.05)
        assert client._keepalive_task and not client._keepalive_task.done()

        client._final_event.set()
        await asyncio.wait_for(close_task, timeout=0.5)

        await asyncio.sleep(0.05)
        assert guard_ws.open is False
        assert client._keepalive_task is None

    asyncio.run(run())


def test_send_warns_but_continues_after_open_timeout(monkeypatch, caplog):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    caplog.set_level(logging.WARNING)

    async def run():
        client = dg_mod.DeepgramClient()
        client._ws = DummyWS()
        client._open_wait_s = 0.0
        client._min_valid_bytes = 1

        await client.send(b"a" * 4)
        await client.send(b"b" * 4)
        await asyncio.sleep(0.06)
        return client

    client = asyncio.run(run())

    assert client._ws.sent == [b"a" * 4, b"b" * 4]
    assert client._open_evt.is_set()
    assert client._open_gate_warned is True
    assert caplog.text.count("Deepgram send gated but no open within timeout") == 1
    event = client._ev_queue.get_nowait()
    assert event["type"] == "asr_open"
    with pytest.raises(asyncio.QueueEmpty):
        client._ev_queue.get_nowait()


def test_send_does_not_wait_for_provider_ready(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    release_iter = asyncio.Event()

    class SlowDummyWS(DummyWS):
        def __aiter__(self):
            async def _gen():
                await release_iter.wait()
                yield json.dumps({"type": "Metadata"})

            return _gen()

    async def fake_connect(url, **kwargs):
        return SlowDummyWS()

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()
        await client.connect()
        client._min_valid_bytes = 1

        send_task = asyncio.create_task(client.send(b"a" * 4))
        await asyncio.sleep(0)

        # Audio should be sent immediately without waiting for metadata frames.
        assert json.loads(client._ws.sent[0])["type"] == "Configure"
        async def _wait_for_audio() -> list:
            deadline = asyncio.get_running_loop().time() + 0.3
            while True:
                non_keepalive = [
                    item
                    for item in client._ws.sent[1:]
                    if not (
                        isinstance(item, str)
                        and json.loads(item).get("type") == "KeepAlive"
                    )
                ]
                if non_keepalive:
                    return non_keepalive
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("audio chunk not sent before metadata")
                await asyncio.sleep(0.01)

        non_keepalive = await _wait_for_audio()
        assert non_keepalive[0] == b"a" * 4

        release_iter.set()
        await send_task

        event = await asyncio.wait_for(client._ev_queue.get(), timeout=0.1)
        assert event["type"] == "asr_open"

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_dg_url_tag_includes_session(monkeypatch):
    monkeypatch.delenv("DG_URL_TAG", raising=False)
    url = dg_mod._dg_url({"session_id": "user$sess", "_transport": {"containerized_opus": False}})
    qs = parse_qs(urlparse(url).query)
    assert qs.get("tag"), url
    assert qs["tag"][0] == "sid:user_sess"


def test_dg_url_tag_combines_env_and_override(monkeypatch):
    monkeypatch.setenv("DG_URL_TAG", "build42")
    url = dg_mod._dg_url({"_url_tag": "custom tag!!", "_transport": {"containerized_opus": True}})
    qs = parse_qs(urlparse(url).query)
    assert qs.get("tag"), url
    assert qs["tag"][0] == "build42:custom_tag__"


def test_structured_logs_cover_open_forward_close(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    events = []

    def capture(event, **data):
        events.append((event, data))

    dummy_ws = DummyWS()

    async def fake_connect(url, **kwargs):
        return dummy_ws

    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient({
            "_jlog": capture,
            "session_id": "sess-123",
            "_url_tag": "tag*value",
        })

        await client.connect()
        await client.send(b"a" * 96)
        await client._flush_tx()
        await client.close(wait_for_final=False)

    asyncio.run(run())

    names = [evt for evt, _ in events]
    assert "dg_open" in names
    assert "dg_forward" in names
    assert "dg_close" in names

    tags = [data.get("tag") for evt, data in events if evt in {"dg_open", "dg_forward", "dg_close"}]
    assert tags, events
    assert all(tag == "tag_value" for tag in tags)
