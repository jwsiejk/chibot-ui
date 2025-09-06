# app/asgi_gateway.py
# WS -> chat router; HTTP -> serve /static via ASGI fast-path, everything else -> Flask WSGI.
import os
from app import create_app
try:
    from asgiref.wsgi import WsgiToAsgi
except Exception:
    class WsgiToAsgi:
        def __init__(self, app): self.app = app
        async def __call__(self, scope, receive, send):
            raise RuntimeError("WsgiToAsgi shim used in test env")

# Eagerly create Flask app for workers & tests expecting `app`
app = create_app()

# Wrap as ASGI for uvicorn
asgi = WsgiToAsgi(app)

STATIC_PREFIX = os.environ.get("STATIC_URL_PATH", "/static")

async def _serve_static(scope, receive, send):
    await asgi(scope, receive, send)

try:
    from .ws_asgi import ws_asgi  # optional
except Exception:
    ws_asgi = None

async def __call__(scope, receive, send):
    typ = scope.get("type")
    if typ == "websocket" and scope.get("path","").startswith("/ws/v1/"):
        if ws_asgi:
            await ws_asgi(scope, receive, send)
            return
    path = scope.get("path", "")
    if typ == "http" and (path.startswith(STATIC_PREFIX + "/") or path == "/favicon.ico"):
        await _serve_static(scope, receive, send)
        return
    await asgi(scope, receive, send)
