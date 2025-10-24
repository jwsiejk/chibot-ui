"""Tests for websocket route matching with varying ASGI root_path values."""

from app.asgi_gateway import WS_ROUTE, _ws_route_matches


def make_scope(*, path: str, root_path: str = "") -> dict:
    return {"type": "websocket", "path": path, "root_path": root_path}


def test_ws_route_matches_without_root_path():
    scope = make_scope(path="/ws/v2/chat", root_path="")
    assert _ws_route_matches(scope, WS_ROUTE) is True


def test_ws_route_matches_with_root_path():
    scope = make_scope(path="/ws/v2/chat", root_path="/chibot-ui")
    assert _ws_route_matches(scope, WS_ROUTE) is True


def test_ws_route_matches_with_prefixed_mount():
    scope = make_scope(path="/sub/ws/v2/chat", root_path="/prefix")
    assert _ws_route_matches(scope, WS_ROUTE) is True


def test_ws_route_mismatch():
    scope = make_scope(path="/ws/v3/chat", root_path="")
    assert _ws_route_matches(scope, WS_ROUTE) is False
    scope = make_scope(path="/other", root_path="/prefix")
    assert _ws_route_matches(scope, WS_ROUTE) is False
