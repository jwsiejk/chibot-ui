# app/ws/ws_dispatch.py
from typing import Callable, Awaitable, Dict, Any
from app.ws.ws_asgi import _ws_chat_asgi_impl
from app.ws.ws_probe import ws_probe

ASGIApp = Callable[[Dict[str, Any], Callable, Callable], Awaitable[None]]

async def ws_dispatch(scope, receive, send):
    """
    Catch-all WS dispatcher mounted at /ws.
    Ensures any /ws/* path stays in ASGI and never falls through to WSGI.
    """
    if scope.get("type") != "websocket":
        # Not a WS; refuse as 404 so WSGI can handle plain HTTP at /
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})
        return

    path = (scope.get("path") or "")  # already starts with /ws/...
    # Route subpaths explicitly
    if path.startswith("/ws/_probe"):
        return await ws_probe(scope, receive, send)
    if path.startswith("/ws/v1/chat"):
        return await _ws_chat_asgi_impl(scope, receive, send)

    # Unknown /ws subpath → close cleanly
    try:
        await send({"type": "websocket.accept", "subprotocol": "probe"})
    except Exception:
        pass
    try:
        await send({"type": "websocket.close", "code": 1000, "reason": "unknown_ws_route"})
    except Exception:
        pass
