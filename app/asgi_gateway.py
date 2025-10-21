"""ASGI gateway mounting the chat.v2 adapter and health probe."""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from app.ws.adapter import CHAT_V2_SUBPROTOCOL, ChatV2Adapter

WS_ROUTE = "/ws/v2/chat"
HEALTH_ROUTE = "/api/v1/health"

_adapter: Optional[ChatV2Adapter] = None


async def app(scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
    """Dispatch incoming ASGI scopes to HTTP handlers or the chat adapter."""
    scope_type = scope.get("type")
    path = (scope.get("root_path") or "") + scope.get("path", "")

    if scope_type == "websocket":
        if path == WS_ROUTE:
            await _get_adapter()(scope, receive, send)
        else:
            await _reject_websocket(receive, send)
        return

    if scope_type == "http":
        if path == HEALTH_ROUTE:
            await _handle_health(scope, receive, send)
        else:
            await _drain_request_body(receive)
            await _send_json_response(send, {"error": "not_found"}, status=404)
        return

    raise RuntimeError(f"Unsupported ASGI scope type: {scope_type}")


async def _handle_health(
    scope: dict, receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]
) -> None:
    """Return a static JSON document for health checks."""
    method = scope.get("method", "GET").upper()
    if method != "GET":
        await _drain_request_body(receive)
        await _send_json_response(send, {"error": "method_not_allowed"}, status=405)
        return

    await _drain_request_body(receive)
    payload = {"ok": True, "engine": "v2", "ws_subprotocol": CHAT_V2_SUBPROTOCOL}
    await _send_json_response(send, payload, status=200)


async def _drain_request_body(receive: Callable[[], Awaitable[dict]]) -> None:
    """Consume and discard the HTTP request body if present."""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        if not message.get("more_body"):
            break


async def _reject_websocket(receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
    """Politely refuse WebSocket connections on unsupported routes."""
    message = await receive()
    if message.get("type") == "websocket.connect":
        await send({"type": "websocket.close", "code": 1000})


async def _send_json_response(send: Callable[[dict], Awaitable[None]], payload: Any, *, status: int) -> None:
    """Serialize JSON payload and emit a minimal HTTP response."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


__all__ = ["app"]


def _get_adapter() -> ChatV2Adapter:
    """Return a lazily instantiated ChatV2Adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = ChatV2Adapter()
    return _adapter
