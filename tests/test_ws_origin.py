"""Origin allow-list enforcement for WebSocket handshakes."""
from __future__ import annotations

import os
from typing import Optional
from unittest.mock import patch

import pytest

from app.asgi_gateway import _validate_ws_origin


def _make_scope(origin: Optional[str]) -> dict:
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode("latin1")))
    return {"type": "websocket", "path": "/ws/v2/chat", "headers": tuple(headers)}


@pytest.mark.parametrize(
    "config, origin, expected",
    [
        ({"ASKCHIP_WS_ALLOWED_ORIGINS": "https://app.askchip.ai"}, "https://app.askchip.ai", True),
        ({"ASKCHIP_WS_ALLOWED_ORIGINS": "https://app.askchip.ai"}, "https://evil.example", False),
        ({"ASKCHIP_ENV": "development"}, "http://localhost:3000", True),
        ({"ASKCHIP_ENV": "prod"}, "http://localhost:3000", False),
    ],
)
def test_origin_policy(config: dict[str, str], origin: str, expected: bool) -> None:
    with patch.dict(os.environ, config, clear=True):
        allowed, _ = _validate_ws_origin(_make_scope(origin))
        assert allowed is expected


def test_missing_origin_is_accepted() -> None:
    with patch.dict(os.environ, {}, clear=True):
        allowed, origin = _validate_ws_origin(_make_scope(None))
        assert origin is None
        assert allowed is True
