"""Smoke tests covering shared auth paths for WS and admin HTTP."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List
from unittest import mock

from app.asgi_gateway import _enforce_admin_auth, json_response
from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter


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
    return json_response(ok=True)


async def _admin_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _run_admin(scope: Dict[str, Any]) -> Any:
    return asyncio.run(_enforce_admin_auth(_admin_handler, scope, _admin_receive))


def test_ws_valid_query_token_allows_upgrade() -> None:
    scope = _ws_scope(query_string=b"access_token=good")
    events = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ]
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "1"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt", return_value=(True, None, {"sub": "demo"})):
            sent = _drive_ws(scope, events)

    accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
    assert accepts and accepts[0].get("subprotocol") == CHAT_V2_SUBPROTOCOL

    payloads = [json.loads(msg["text"]) for msg in sent if msg.get("type") == "websocket.send"]
    assert all(frame.get("error") != "unauthorized" for frame in payloads)


def test_ws_invalid_token_closes_with_error() -> None:
    scope = _ws_scope(query_string=b"access_token=bad")
    events = [{"type": "websocket.connect"}]
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "1"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt", return_value=(False, "bad token", None)):
            sent = _drive_ws(scope, events)

    payloads = [json.loads(msg["text"]) for msg in sent if msg.get("type") == "websocket.send"]
    assert payloads == [{"type": "error", "error": "unauthorized", "detail": "bad token"}]

    closes = [msg for msg in sent if msg.get("type") == "websocket.close"]
    assert closes and closes[0].get("code") == 1008


def test_ws_dev_mode_allows_missing_token() -> None:
    scope = _ws_scope()
    events = [
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ]
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "0"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt") as verify:
            sent = _drive_ws(scope, events)
    verify.assert_not_called()

    accepts = [msg for msg in sent if msg.get("type") == "websocket.accept"]
    assert accepts


def test_admin_http_valid_token_allows_request() -> None:
    scope = {
        "type": "http",
        "query_string": b"access_token=good",
        "headers": [],
    }
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "1"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt", return_value=(True, None, {"sub": "demo"})):
            response = _run_admin(scope)

    assert response.status == 200
    assert json.loads(response.body.decode("utf-8")) == {"ok": True}


def test_admin_http_invalid_token_returns_401() -> None:
    scope = {
        "type": "http",
        "query_string": b"access_token=bad",
        "headers": [],
    }
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "1"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt", return_value=(False, "bad token", None)):
            response = _run_admin(scope)

    assert response.status == 401
    body = json.loads(response.body.decode("utf-8"))
    assert body == {"error": "unauthorized", "detail": "bad token"}


def test_admin_http_dev_mode_allows_missing_token() -> None:
    scope = {"type": "http", "query_string": b"", "headers": []}
    with mock.patch.dict(os.environ, {"ASKCHIP_ENV": "development", "WS_TOKEN_REQUIRED": "0"}, clear=False):
        with mock.patch("app.security.auth.verify_jwt") as verify:
            response = _run_admin(scope)
    verify.assert_not_called()

    assert response.status == 200
    assert json.loads(response.body.decode("utf-8")) == {"ok": True}
