from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter
from app.asgi_gateway import _enforce_admin_auth as enforce_admin_auth  # type: ignore[attr-defined]


def _ws_scope(**overrides: Any) -> Dict[str, Any]:
    scope: Dict[str, Any] = {
        "type": "websocket",
        "subprotocols": [CHAT_V2_SUBPROTOCOL],
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    scope.update(overrides)
    return scope


async def _run_ws(adapter: ChatV2Adapter, scope: Dict[str, Any], events: List[dict]) -> List[dict]:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for event in events:
        queue.put_nowait(event)

    sent: List[dict] = []

    async def receive() -> dict:
        return await queue.get()

    async def send(message: dict) -> None:
        sent.append(message)

    await adapter(scope, receive, send)
    return sent


def _drive_ws(scope: Dict[str, Any], events: List[dict]) -> List[dict]:
    adapter = ChatV2Adapter()
    return asyncio.run(_run_ws(adapter, scope, events))


async def _admin_handler(scope: Dict[str, Any], receive: Any) -> Any:  # pragma: no cover - signature stub
    from app.asgi_gateway import json_response

    return json_response(ok=True)


async def _admin_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _run_admin(scope: Dict[str, Any]) -> Any:
    return asyncio.run(enforce_admin_auth(_admin_handler, scope, _admin_receive))


def test_ws_accepts_connections_without_tokens() -> None:
    scope = _ws_scope()
    events = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ]
    with patch_env({"ASKCHIP_ENV": "production", "WS_TOKEN_REQUIRED": "1"}):
        sent = _drive_ws(scope, events)

    accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
    assert accepts and accepts[0].get("subprotocol") == CHAT_V2_SUBPROTOCOL

    payloads = [json.loads(msg["text"]) for msg in sent if msg.get("type") == "websocket.send"]
    assert all(frame.get("error") != "unauthorized" for frame in payloads)


def test_ws_handles_authorization_header() -> None:
    scope = _ws_scope(headers=[(b"authorization", b"Bearer ignored")])
    events = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ]
    sent = _drive_ws(scope, events)

    accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
    assert accepts and accepts[0].get("subprotocol") == CHAT_V2_SUBPROTOCOL

    payloads = [json.loads(msg["text"]) for msg in sent if msg.get("type") == "websocket.send"]
    assert all(frame.get("error") != "unauthorized" for frame in payloads)


def test_admin_http_requests_succeed_without_token() -> None:
    scope = {
        "type": "http",
        "query_string": b"",
        "headers": [],
    }
    response = _run_admin(scope)

    assert response.status == 200
    assert json.loads(response.body.decode("utf-8")) == {"ok": True}


class patch_env:
    """Context manager to temporarily set environment variables."""

    def __init__(self, mapping: Dict[str, str]):
        self._mapping = mapping
        self._originals: Dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._mapping.items():
            self._originals[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, previous in self._originals.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self._originals.clear()
