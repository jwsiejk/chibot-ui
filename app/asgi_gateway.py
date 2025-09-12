from __future__ import annotations

import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template, make_response, abort
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

APP_VERSION = os.environ.get("APP_VERSION", "v1")
ENV_NAME = os.environ.get("ENV_NAME", "prod")

# ------------------------------------------------------------------
# Flask (HTTP v1 APIs + UI landing, security headers)
# ------------------------------------------------------------------
flask_app = Flask(__name__, template_folder="templates", static_folder="static")

# Security headers for every response
@flask_app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # Basic CSP allowing our own assets only
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'")
    # Long HSTS only if https behind proxy; safe in Render
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
    return resp

# v1 health blueprint
from app.api_v1.health import bp as health_bp
flask_app.register_blueprint(health_bp)

# Landing page (GET /) with version/ts and links; HEAD / is separately handled.
@flask_app.get("/")
def root_page():
    return render_template("index.html",
        version=APP_VERSION,
        env=ENV_NAME,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    ), 200

# Quiet generic HEAD / pings
@flask_app.route("/", methods=["HEAD"])
def _root_head():
    return ("", 204)

# robots.txt + favicon to avoid 404 churn in logs
@flask_app.get("/robots.txt")
def robots_txt():
    resp = make_response("User-agent: *\nDisallow:\n", 200)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp

@flask_app.get("/favicon.ico")
def favicon():
    # 1x1 transparent PNG
    from base64 import b64decode
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/avJkqkAAAAASUVORK5CYII=")
    resp = make_response(png, 200)
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

# JSON 404 for API paths; HTML for others
@flask_app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify(error="not_found", path=request.path), 404
    return render_template("404.html", path=request.path), 404

# Clean 405
@flask_app.errorhandler(405)
def handle_405(e):
    if request.path.startswith("/api/"):
        return jsonify(error="method_not_allowed", path=request.path), 405
    return render_template("405.html", path=request.path), 405

# ------------------------------------------------------------------
# Starlette (WS v1)
# ------------------------------------------------------------------
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
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
