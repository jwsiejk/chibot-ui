from app.ws.ws_asgi import _ws_chat_asgi_impl
# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI via _ws_chat_asgi_impl)
#   • All HTTP via mounted Flask WSGI app
import asyncio, time
from app import create_app
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute, Mount
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from app.ws.protocol import dumps, PROTO_ID, DEFAULT_HEARTBEAT_MS

# On import, log what ws handler is bound to
print(">>> asgi_gateway loaded, using _ws_chat_asgi_impl from:", getattr(_ws_chat_asgi_impl, "__module__", _ws_chat_asgi_impl))

# Optional admin log emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    def _admin_emit(*a, **k): pass

# Build the Flask WSGI app
flask_app = create_app()

async def _keepalive_task(websocket, heartbeat_ms: int):
    try:
        while True:
            await asyncio.sleep(max(heartbeat_ms, 1000) / 1000.0)
            try:
                await websocket.send_text(dumps({
                    "type": "keepalive",
                    "ts": int(time.time() * 1000)
                }))
            except Exception:
                break
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

# --- Compose Starlette app ---
routes = [
    WebSocketRoute("/ws/v1/chat", _ws_chat_asgi_impl),  # bind directly to ASGI handler
    Mount("/", app=WSGIMiddleware(flask_app)),
]
print(">>> routes configured:", routes)

_cors_origins = []
import os as _os
_allow = _os.environ.get("CORS_ALLOWLIST", "").strip()
if _allow:
    _cors_origins = [o.strip() for o in _allow.split(",") if o.strip()]

middleware = [Middleware(GZipMiddleware, minimum_size=1024)]
if _cors_origins:
    middleware.insert(0, Middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        allow_credentials=True
    ))

asgi = Starlette(routes=routes, middleware=middleware)

# Test-compat alias for Flask test_client usage
app = flask_app

# Provide Flask test_client for tests that call asgi.test_client()
asgi.test_client = flask_app.test_client

# Test helper: expose Flask url_map on asgi for tests expecting it
asgi.url_map = flask_app.url_map
