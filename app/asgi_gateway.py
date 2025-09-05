# app/asgi_gateway.py
# Phase1 patch: optional asgiref
try:
    from asgiref.wsgi import WsgiToAsgi
except Exception:
    class WsgiToAsgi:
        def __init__(self, app):
            self.app = app
        def __call__(self, scope, receive, send):
            raise RuntimeError('WsgiToAsgi shim used in test env')


from . import create_app            # your Flask factory (WSGI)
from .asgi_router import asgi as ws # your ASGI WS router (/ws/v1/chat)

# Build the WSGI Flask app and wrap it for ASGI
_flask = create_app()
# Phase1 expose Flask app for tests
app = _flask
flask_app = _flask
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

# Auto-registered by Phase1 patch
try:
    from app.routes.voice import bp_voice
    app.register_blueprint(bp_voice)
except Exception as _e:
    print('Phase1: could not register voice blueprint', _e)
