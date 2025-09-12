from __future__ import annotations

# --- Standard imports ---
from flask import Flask, request, jsonify
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

# ------------------------------------------------------------------
# Flask (HTTP v1 APIs)
# ------------------------------------------------------------------
flask_app = Flask(__name__)

# v1: health (NEW)
from app.api_v1.health import bp as health_bp
flask_app.register_blueprint(health_bp)

# Optional: quiet generic HEAD / pings from proxies (does not add non-v1 APIs)
@flask_app.route("/", methods=["HEAD"])
def _root_head():
    return ("", 204)

# EXAMPLE: keep your other v1 routes here (admin/config, voice, etc.)
# (Leave as-is in your repo; below are placeholders that are safe to keep or remove.)
@flask_app.get("/api/v1/healthz")  # optional secondary ping
def _healthz():
    return jsonify(ok=True), 200

# ------------------------------------------------------------------
# Starlette (WS v1)
# ------------------------------------------------------------------
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Your real WS logic runs here; we keep a minimal heartbeat to stay valid.
            await websocket.send_json({"type": "state", "phase": "ready"})
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass

asgi = Starlette()
asgi.add_websocket_route("/ws/v1/chat", ws_endpoint)
asgi.mount("/", app=WSGIMiddleware(flask_app))

# CORS (tuned if you split UI/API; harmless otherwise)
asgi.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
