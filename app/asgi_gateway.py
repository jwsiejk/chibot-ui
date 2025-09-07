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
async def chat_ws(websocket):
    # Starlette WebSocket endpoint
    await websocket.accept()
    try:
        session_id = websocket.query_params.get("session_id", "")
        # Tell client we're alive so UI can proceed to greet
        await websocket.send_text(json.dumps({"type":"ready","session_id": session_id}))
        while True:
            message = await websocket.receive()
            t = message.get("type")
            if t == "websocket.receive":
                if "text" in message and message["text"] == "ping":
                    await websocket.send_text("pong")
            elif t == "websocket.disconnect":
                break
    finally:
        try:
            await websocket.close(code=1000)
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
