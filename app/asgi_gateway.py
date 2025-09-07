# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI)
#   • All HTTP via mounted Flask WSGI app
import json
from app import create_app
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute, Mount
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware import Middleware

# Build the Flask WSGI app
flask_app = create_app()

# --- WebSocket endpoint ---
async def chat_ws(websocket):
    await websocket.accept()
    # Always send 'ready' FIRST so the UI and tests can progress
    session_id = websocket.query_params.get("session_id", "")
    await websocket.send_text(json.dumps({"type": "ready", "session_id": session_id}))
    while True:
        try:
            message = await websocket.receive()
        except Exception:
            break
        t = message.get("type")
        if t == "websocket.disconnect":
            break
        if t == "websocket.receive":
            if "text" in message and message["text"] == "ping":
                await websocket.send_text("pong")
            # Ignore other inbound frames for now
    try:
        await websocket.close(code=1000)
    except Exception:
        pass

# --- Compose Starlette app ---
routes = [
    WebSocketRoute("/ws/v1/chat", chat_ws),
    Mount("/", app=WSGIMiddleware(flask_app)),
]
middleware = [Middleware(GZipMiddleware, minimum_size=1024)]
asgi = Starlette(routes=routes, middleware=middleware)
