from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, Tuple

import pytest

from app import asgi_gateway


def _make_scope(path: str, origin: str | None) -> Dict[str, object]:
    headers: list[Tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode("latin1")))
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers,
    }


def _make_receive() -> Callable[[], Awaitable[Dict[str, object]]]:
    messages = [
        {"type": "http.request", "body": b"", "more_body": False},
    ]

    async def _receive() -> Dict[str, object]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return _receive


@pytest.mark.parametrize(
    "env",
    ["dev", "staging"],
)
def test_admin_cors_enabled_for_non_prod(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("ASKCHIP_ENV", env)
    monkeypatch.setenv("ASKCHIP_ADMIN_CORS_ORIGINS", "http://localhost:3000")

    handler = asgi_gateway._resolve_admin_route("/api/v1/admin/flow/sid-123/trace")
    assert handler is not None

    scope = _make_scope("/api/v1/admin/flow/sid-123/trace", "http://localhost:3000")
    response = asyncio.run(handler(scope, _make_receive()))

    header_map = dict(response.headers)
    assert header_map[b"access-control-allow-origin"] == b"http://localhost:3000"
    assert header_map[b"access-control-allow-methods"] == b"GET"


def test_admin_cors_respects_origin_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKCHIP_ENV", "dev")
    monkeypatch.setenv("ASKCHIP_ADMIN_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

    handler = asgi_gateway._resolve_admin_route("/api/v1/admin/flow/sid-123/trace")
    assert handler is not None

    scope = _make_scope("/api/v1/admin/flow/sid-123/trace", "http://127.0.0.1:3000")
    response = asyncio.run(handler(scope, _make_receive()))

    header_map = dict(response.headers)
    assert header_map[b"access-control-allow-origin"] == b"http://127.0.0.1:3000"


def test_admin_cors_disabled_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASKCHIP_ENV", "prod")
    monkeypatch.setenv("ASKCHIP_ADMIN_CORS_ORIGINS", "http://localhost:3000")

    handler = asgi_gateway._resolve_admin_route("/api/v1/admin/flow/sid-123/trace")
    assert handler is not None

    scope = _make_scope("/api/v1/admin/flow/sid-123/trace", "http://localhost:3000")
    response = asyncio.run(handler(scope, _make_receive()))

    header_map = dict(response.headers)
    assert b"access-control-allow-origin" not in header_map
    assert b"access-control-allow-methods" not in header_map
