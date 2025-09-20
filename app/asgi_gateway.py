from app.ws.ws_asgi import _ws_chat_asgi_impl
from app.ws.ws_probe import ws_probe  # probe endpoint for infra checks
import app.logging_setup as _logsetup; _logsetup.install()

# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI via _ws_chat_asgi_impl)
#   • WebSocket /ws/_probe   (minimal probe to verify WS path)
#   • All HTTP via mounted Flask WSGI app

import asyncio, time, os as _os
from app import create_app
from starlette.applications import Starlette
from starlette.routing import Mount
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

# ---- Middleware to log ANY websocket scope hitting the gateway ----
class WSArrivalMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            try:
                _admin_emit("ws_scope_arrived", path=scope.get("path"))
            except Exception:
                pass
        return await self.app(scope, receive, send)
# -------------------------------------------------------------------

# --- Compose Starlette app (WS mounts FIRST, WSGI LAST) ---
routes = [
    Mount("/ws/v1/chat", app=_ws_chat_asgi_impl),  # ASGI WS chat handler
    Mount("/ws/_probe", app=ws_probe),             # ASGI WS probe
    Mount("/", app=WSGIMiddleware(flask_app)),     # WSGI (HTTP) catch-all LAST
]

_cors_origins = []
_allow = _os.environ.get("CORS_ALLOWLIST", "").strip()
if _allow:
    _cors_origins = [o.strip() for o in _allow.split(",") if o.strip()]

middleware = [Middleware(GZipMiddleware, minimum_size=1024),
              Middleware(WSArrivalMiddleware)]
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

# Print route order for sanity in deploy logs
try:
    print(">>> ROUTES:", [getattr(r, "path", "<no-path>") for r in routes])
except Exception:
    pass
