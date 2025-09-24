import asyncio
import json
import logging
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

        event = await client._ev_queue.get()
        assert event["type"] == "asr_open"

        await client.close(wait_for_final=False)

    asyncio.run(run())


def test_connect_emits_asr_open_immediately(monkeypatch):
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

        event = await asyncio.wait_for(client._ev_queue.get(), timeout=0.05)
        assert event["type"] == "asr_open"
        assert client._asr_open_emitted is True

        release_iter.set()
        await asyncio.sleep(0)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client._ev_queue.get(), timeout=0.02)

        await client.close(wait_for_final=False)

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
        return client

    client = asyncio.run(run())

    assert client._ws.sent == [b"a" * 4, b"b" * 4]
    assert client._open_evt.is_set()
    assert client._open_gate_warned is True
    assert caplog.text.count("Deepgram send gated but no open within timeout") == 1
