import asyncio
import json
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
        async def _empty():
            if False:
                yield None
        return _empty()


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
