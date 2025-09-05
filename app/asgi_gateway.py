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

from . import create_app
from .asgi_router import asgi as ws_asgi

# Build once
_wsgi_app = create_app()
_asgi_wsgi = WsgiToAsgi(_wsgi_app)

# Static directory: …/src/static
_STATIC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "static"))
_static_asgi = StaticFiles(directory=_STATIC_DIR)

STATIC_PREFIX = "/static"

async def _serve_static(scope, receive, send):
    """
    Rewrites scope['path'] so StaticFiles sees a path relative to the static dir.
    /static/js/app.js -> /js/app.js
    """
    path = scope.get("path", "")
    if path == "/favicon.ico":
        rel = "/favicon.ico"
    else:
        # must start with /static/ at this point
        rel = path[len(STATIC_PREFIX):]  # drop '/static'
        if not rel.startswith("/"):
            rel = "/" + rel
    new_scope = dict(scope)
    new_scope["path"] = rel
    await _static_asgi(new_scope, receive, send)

async def asgi(scope, receive, send):
    typ = scope.get("type")
    if typ == "websocket":
        await ws_asgi(scope, receive, send)
        return

    path = scope.get("path", "")
    if typ == "http" and (path.startswith(STATIC_PREFIX + "/") or path == "/favicon.ico"):
        await _serve_static(scope, receive, send)
        return

    await _asgi_wsgi(scope, receive, send)
