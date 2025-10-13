import asyncio
import json
import logging
from collections import deque
from websockets import protocol as ws_protocol
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


class AsyncioClientWS:
    """Minimal stub that mimics asyncio-style websockets.client connection."""

    def __init__(self):
        self.state = ws_protocol.State.CONNECTING
        self.sent = []
        self.close_called = False

    @property
    def closed(self) -> bool:
        return self.state in (ws_protocol.State.CLOSING, ws_protocol.State.CLOSED)

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.state = ws_protocol.State.CLOSED
        self.close_called = True

    def __aiter__(self):
        async def _gen():
            yield json.dumps({"type": "Metadata"})

        return _gen()


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


def test_connect_emits_admin_event_once(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    events = []

    def capture(event, **payload):
        events.append((event, payload))

    async def fake_connect(url, **kwargs):
        return DummyWS()

    monkeypatch.setattr(dg_mod, "_admin_emit", capture)
    monkeypatch.setattr(dg_mod.websockets, "connect", fake_connect)

    async def run():
        client = dg_mod.DeepgramClient()

        await client.connect()

        assert len(events) == 1
        event, payload = events[0]
        assert event == "dg_connect"
        assert payload["session_id"]
        assert payload["safe_url"].startswith("wss://")
        assert isinstance(payload["containerized"], bool)
        assert payload["test_mode"] is False
        assert isinstance(payload["elapsed_ms"], int)

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_connect_emits_admin_event_once_in_test_mode(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", True)

    events = []

    def capture(event, **payload):
        events.append((event, payload))

    monkeypatch.setattr(dg_mod, "_admin_emit", capture)

    async def run():
        client = dg_mod.DeepgramClient({"session_id": "sid-admin"})

        await client.connect()

        assert len(events) == 1
        event, payload = events[0]
        assert event == "dg_connect"
        assert payload["session_id"] == "sid-admin"
        assert payload["test_mode"] is True
        assert isinstance(payload["containerized"], bool)

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


def test_close_retries_flush_until_socket_open(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    class DelayedOpenWS(DummyWS):
        def __init__(self):
            super().__init__()
            self.open = False

    async def run():
        client = dg_mod.DeepgramClient()
        ws = DelayedOpenWS()
        client._ws = ws
        client._open_evt.set()
        client._min_valid_bytes = 1
        client._tx_queue.append(b"queued-bytes")

        events = []

        def _capture(event, **payload):
            events.append((event, payload))

        client._jlog = _capture

        async def _enable_open():
            await asyncio.sleep(0.05)
            ws.open = True

        asyncio.create_task(_enable_open())

        await client.close(wait_for_final=False)

        # Data should have been sent once the socket opened
        assert any(item == b"queued-bytes" for item in ws.sent)
        assert any(
            event == "dg_writer_drained" and payload.get("bytes", 0) > 0
            for event, payload in events
        )
        assert client._tx_queue == deque()

    asyncio.run(run())


def test_asyncio_client_connection_flushes_and_closes(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    async def run():
        client = dg_mod.DeepgramClient()
        ws = AsyncioClientWS()
        client._ws = ws
        client._open_evt.set()
        client._min_valid_bytes = 1

        client._tx_queue.append(b"a" * 4)
        if client._drain_event.is_set():
            client._drain_event.clear()

        flush_task = asyncio.create_task(client._flush_tx())
        await asyncio.sleep(0.02)
        assert not flush_task.done()

        ws.state = ws_protocol.State.OPEN
        bytes_sent, _ = await asyncio.wait_for(flush_task, timeout=0.5)

        assert bytes_sent == 4
        assert ws.sent == [b"a" * 4]
        assert client._tx_queue == deque()
        assert client._drain_event.is_set()

        client._tx_queue.extend([b"b" * 4, b"c" * 4])
        if client._drain_event.is_set():
            client._drain_event.clear()

        ws.state = ws_protocol.State.CONNECTING
        close_task = asyncio.create_task(client.close(wait_for_final=False))
        await asyncio.sleep(0.02)
        assert not close_task.done()

        ws.state = ws_protocol.State.OPEN
        await asyncio.wait_for(close_task, timeout=0.5)

        assert ws.close_called is True
        assert client._tx_queue == deque()
        payload_types = [
            json.loads(item).get("type")
            for item in ws.sent
            if isinstance(item, str)
        ]
        assert "CloseStream" in payload_types

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


def test_close_emits_close_stream_ack_diag(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")

    diag_events: list[tuple[str, dict]] = []

    def _capture_diag(label: str, **payload):
        diag_events.append((label, payload))

    async def run():
        client = dg_mod.DeepgramClient({"session_id": "sid-close", "_diag_hook": _capture_diag})
        client._ws = DummyWS()
        client._open_evt.set()
        client._final_event.set()
        await client.close(wait_for_final=False)
        return client

    client = asyncio.run(run())

    ack_payloads = [payload for label, payload in diag_events if label == "CloseStream ack"]
    assert ack_payloads, f"expected CloseStream ack diag event, saw {diag_events}"
    ack_payload = ack_payloads[0]
    assert ack_payload.get("status") == "ok", f"unexpected ack status: {ack_payload}"
    assert ack_payload.get("drain_failed") is False
    assert ack_payload.get("session_id") == "sid-close"
    assert ack_payload.get("provider") == "deepgram"
    assert isinstance(client._dg_id, int)


def test_send_times_out_and_raises_runtime_error(monkeypatch, caplog):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    caplog.set_level(logging.WARNING)

    async def run():
        client = dg_mod.DeepgramClient()
        client._ws = DummyWS()
        client._open_wait_s = 0.0
        client._min_valid_bytes = 1

        with pytest.raises(RuntimeError) as exc1:
            await client.send(b"a" * 4)
        assert "deepgram_not_connected" in str(exc1.value)

        with pytest.raises(RuntimeError) as exc2:
            await client.send(b"b" * 4)
        assert "deepgram_not_connected" in str(exc2.value)

        # Ensure no queue residue from failed attempts.
        assert list(client._tx_queue) == []
        assert not client._open_evt.is_set()

        return client

    client = asyncio.run(run())

    assert client._ws.sent == []
    assert client._open_gate_warned is True
    assert caplog.text.count("Deepgram send gated but no open within timeout") == 1


def test_send_waits_for_provider_ready(monkeypatch):
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

        assert json.loads(client._ws.sent[0])["type"] == "Configure"

        # Audio should not be forwarded before the provider signals ready.
        assert all(
            not isinstance(item, (bytes, bytearray))
            for item in client._ws.sent[1:]
        )

        release_iter.set()

        async def _wait_for_audio_after_ready() -> list:
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
                    raise AssertionError("audio chunk not sent after provider ready")
                await asyncio.sleep(0.01)

        non_keepalive = await _wait_for_audio_after_ready()
        assert non_keepalive[0] == b"a" * 4
        await send_task

        event = await asyncio.wait_for(client._ev_queue.get(), timeout=0.1)
        assert event["type"] == "asr_open"

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_close_after_send_timeout_logs_queue_drained(monkeypatch, caplog):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "abc123")
    monkeypatch.setattr(dg_mod, "DG_TEST_MODE", False)

    caplog.set_level(logging.INFO)

    async def run():
        client = dg_mod.DeepgramClient()
        client._ws = DummyWS()
        client._open_wait_s = 0.0
        client._min_valid_bytes = 1

        with pytest.raises(RuntimeError):
            await client.send(b"hello")

        assert list(client._tx_queue) == []

        await client.close(wait_for_final=False)

    asyncio.run(run())

    assert "dg_writer_drained" in caplog.text
    assert "queued=0" in caplog.text


def test_dg_url_tag_includes_session(monkeypatch):
    monkeypatch.delenv("DG_URL_TAG", raising=False)
    overrides = {"session_id": "user$sess", "_transport": {"containerized_opus": False}}
    url = dg_mod._dg_url(overrides)
    qs = parse_qs(urlparse(url).query)
    assert qs.get("tag"), url
    assert qs["tag"][0] == "sid:user_sess"
    assert overrides["_transport"]["containerized_opus"] is True
    assert overrides["_transport"].get("_containerized_forced") is True


def test_dg_url_tag_combines_env_and_override(monkeypatch):
    monkeypatch.setenv("DG_URL_TAG", "build42")
    overrides = {"_url_tag": "custom tag!!", "_transport": {"containerized_opus": True}}
    url = dg_mod._dg_url(overrides)
    qs = parse_qs(urlparse(url).query)
    assert qs.get("tag"), url
    assert qs["tag"][0] == "build42:custom_tag__"
    assert "_containerized_forced" not in overrides["_transport"]


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
