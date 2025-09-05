# app/asgi_gateway.py
# WS -> chat router; HTTP -> serve /static via ASGI fast-path, everything else -> Flask WSGI.

import os
try:
    from asgiref.wsgi import WsgiToAsgi
except Exception:
    class WsgiToAsgi:
        def __init__(self, app): self.app = app
        async def __call__(self, scope, receive, send):
            raise RuntimeError("WsgiToAsgi shim used in test env")

from starlette.staticfiles import StaticFiles

from . import create_app                  # Flask WSGI (templates, routes)
from .asgi_router import asgi as ws_asgi  # WS router for /ws/v1/chat

# Build once at import
_wsgi_app = create_app()
_asgi_wsgi = WsgiToAsgi(_wsgi_app)

# ASGI static server (bypasses WSGI thread executor)
_STATIC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "static"))
_static_asgi = StaticFiles(directory=_STATIC_DIR)

async def asgi(scope, receive, send):
    typ = scope.get("type")
    if typ == "websocket":
        # WS → chat router
        await ws_asgi(scope, receive, send)
        return

    # Pure ASGI fast path for static files and favicon
    path = scope.get("path", "")
    if typ == "http" and (path.startswith("/static/") or path == "/favicon.ico"):
        await _static_asgi(scope, receive, send)
        return

    # All other HTTP → Flask (via WsgiToAsgi)
    await _asgi_wsgi(scope, receive, send)
