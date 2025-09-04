# app/asgi_gateway.py
from asgiref.wsgi import WsgiToAsgi

from . import create_app            # your Flask factory (WSGI)
from .asgi_router import asgi as ws # your ASGI WS router (/ws/v1/chat)

# Build the WSGI Flask app and wrap it for ASGI
_flask = create_app()
_wsgi_asgi = WsgiToAsgi(_flask)

async def asgi(scope, receive, send):
    typ = scope.get("type")
    if typ == "websocket":
        # All WS (e.g., /ws/v1/chat) are handled by the ASGI router
        return await ws(scope, receive, send)

    if typ == "lifespan":
        # Minimal lifespan support so Uvicorn is happy
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                break
        return

    # Everything else (HTTP) goes to the WSGI-wrapped Flask app
    return await _wsgi_asgi(scope, receive, send)
