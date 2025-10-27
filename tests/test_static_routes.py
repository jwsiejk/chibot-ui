from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from app import asgi_gateway


Headers = List[Tuple[bytes, bytes]]


def _make_scope(path: str, method: str = "GET") -> Dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
    }


def _make_receive(body: bytes = b"") -> Callable[[], Awaitable[Dict[str, Any]]]:
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def _receive() -> Dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    return _receive


def _collect_headers(raw: Headers) -> Dict[bytes, bytes]:
    return {name: value for name, value in raw}


def _dispatch(path: str, method: str = "GET") -> List[Dict[str, Any]]:
    scope = _make_scope(path, method)
    messages: List[Dict[str, Any]] = []

    async def _send(message: Dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(asgi_gateway.app(scope, _make_receive(), _send))
    return messages


def test_root_route_serves_index_html() -> None:
    messages = _dispatch("/")

    assert len(messages) == 2
    start, body = messages
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
    headers = _collect_headers(start["headers"])
    assert headers[b"content-type"] == b"text/html; charset=utf-8"
    assert body["type"] == "http.response.body"
    assert b"id=\"app\"" in body["body"]


def test_static_asset_served_from_app() -> None:
    messages = _dispatch("/static/js/app.js")

    assert len(messages) == 2
    start, body = messages
    assert start["status"] == 200
    headers = _collect_headers(start["headers"])
    assert headers[b"content-type"].startswith(b"text/javascript")
    length = int(headers[b"content-length"].decode("ascii"))
    assert body["type"] == "http.response.body"
    assert length == len(body["body"])
    assert body["body"]


def test_missing_static_asset_returns_not_found() -> None:
    messages = _dispatch("/static/js/missing-file.js")

    assert len(messages) == 2
    start, body = messages
    assert start["status"] == 404
    headers = _collect_headers(start["headers"])
    assert headers[b"content-type"] == b"application/json; charset=utf-8"
    assert body["type"] == "http.response.body"
    assert b"not_found" in body["body"]


def test_admin_ui_asset_uses_static_cache_headers() -> None:
    messages = _dispatch("/admin/ui/config_panel.js")

    assert len(messages) == 2
    start, body = messages
    assert start["status"] == 200
    headers = _collect_headers(start["headers"])
    assert headers[b"cache-control"] == b"public, max-age=31536000, immutable"
    assert b"etag" in headers
    assert body["type"] == "http.response.body"
    length = int(headers[b"content-length"].decode("ascii"))
    assert length == len(body["body"])
