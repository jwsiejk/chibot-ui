# app/asgi_gateway.py
# Compose an ASGI app:
#   • WebSocketRoute -> /ws/v1/chat (native ASGI)
#   • Mount("/", WSGIMiddleware(Flask app)) for HTTP
import os, asyncio, json
from urllib.parse import parse_qs

from app import create_app

# Flask WSGI app
flask_app = create_app()

# --- ASGI WS endpoint ---
async def chat_ws(scope, receive, send):
    if scope["type"] != "websocket":
        # Reject anything that isn't a websocket scope
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"websocket only"})
        return

    # Accept
    await send({"type": "websocket.accept"})

    # Minimal session id extraction (used by client UI for one-tab semantics)
    query = scope.get("query_string", b"").decode("utf-8", "ignore")
    qs = parse_qs(query)
    session_id = (qs.get("session_id") or [""])[0]

    # Notify client we are alive (UI waits for open before greet)
    try:
        await send({"type": "websocket.send", "text": json.dumps({"type":"ready","session_id": session_id})})
    except Exception:
        pass

    # Keep the socket open; relay simple pings; ignore other frames for now.
    try:
        while True:
            evt = await receive()
            t = evt.get("type")
            if t == "websocket.receive":
                if "text" in evt and evt["text"] == "ping":
                    await send({"type":"websocket.send","text":"pong"})
                # Ignore other inbound messages; real streaming is driven by HTTP events.
            elif t == "websocket.disconnect":
                break
            else:
                # ignore any other ASGI events
                await asyncio.sleep(0)
    finally:
        try:
            await send({"type":"websocket.close", "code": 1000})
        except Exception:
            pass

# --- Build Starlette app that can handle WS + mount Flask for HTTP ---
try:
    from starlette.applications import Starlette
    from starlette.routing import WebSocketRoute, Mount
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.staticfiles import StaticFiles
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.errors import ServerErrorMiddleware
    from starlette.middleware import Middleware
    from starlette.responses import Response
    from starlette.types import ASGIApp, Receive, Scope, Send
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
    from starlette.middleware import Middleware
except Exception as e:
    # If starlette isn't available in this environment, fail loudly for visibility
    raise

from starlette.middleware.wsgi import WSGIMiddleware

routes = [
    WebSocketRoute("/ws/v1/chat", chat_ws),
    Mount("/", app=WSGIMiddleware(flask_app)),
]

middleware = [
    Middleware(GZipMiddleware, minimum_size=1024),
]

asgi = Starlette(routes=routes, middleware=middleware)
