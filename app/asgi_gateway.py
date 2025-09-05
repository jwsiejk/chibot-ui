# app/asgi_gateway.py
# Unified ASGI entrypoint that proxies HTTP to Flask (serves /static) and WS to our chat router.

try:
    from asgiref.wsgi import WsgiToAsgi
except Exception:
    class WsgiToAsgi:
        def __init__(self, app): self.app = app
        async def __call__(self, scope, receive, send):
            raise RuntimeError("WsgiToAsgi shim used in test env")

from . import create_app                  # Flask WSGI app factory (serves templates + /static)
from .asgi_router import asgi as ws_asgi  # WS router for /ws/v1/chat

# Build once at import
_wsgi_app = create_app()
_asgi_wsgi = WsgiToAsgi(_wsgi_app)

async def asgi(scope, receive, send):
    typ = scope.get("type")
    if typ == "websocket":
        # WS goes to our chat ASGI
        await ws_asgi(scope, receive, send)
        return
    # Everything else (HTTP, lifespan) goes to Flask WSGI wrapped for ASGI
    await _asgi_wsgi(scope, receive, send)
