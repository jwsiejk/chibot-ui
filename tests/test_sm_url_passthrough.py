import asyncio
from unittest.mock import AsyncMock

from app.services.asr.sm_rt import SMRealtimeClient


def test_rt_uses_full_url(monkeypatch):
    url = "wss://eu2.rt.speechmatics.com/v2"
    client = SMRealtimeClient("sid-test")

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_connect = AsyncMock(return_value=mock_ws)
    monkeypatch.setattr("app.services.asr.sm_rt.websockets.connect", mock_connect)

    client._await_ready = AsyncMock(return_value=[])
    client._receive_loop = AsyncMock()
    client._pcm_sender_loop = AsyncMock()
    client._ws_ping_loop = AsyncMock()

    token = "dummy.jwt"
    params: dict = {}

    async def run_test():
        await client.open(endpoint_url=url, jwt_token=token, params=params)
        assert client._url.startswith(f"{url}?jwt=")
        client._sender_task = None
        client._receiver_task = None
        client._ws_ping_task = None
        await client.close()

    asyncio.run(run_test())
