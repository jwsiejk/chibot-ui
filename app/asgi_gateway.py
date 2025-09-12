from __future__ import annotations

import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template, make_response, abort, redirect, url_for
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

APP_VERSION = os.environ.get("APP_VERSION", "v1")
ENV_NAME = os.environ.get("ENV_NAME", "prod")

flask_app = Flask(__name__, template_folder="templates", static_folder="static")

@flask_app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
    return resp

# v1 health
from app.api_v1.health import bp as health_bp
flask_app.register_blueprint(health_bp)

# === UI ROUTES ===
@flask_app.get("/")
def _root_redirect_to_app():
    # Always take users to the real app UI
    return redirect(url_for("render_app_ui"), code=302)

@flask_app.get("/app")
def render_app_ui():
    return render_template("app.html",
        version=APP_VERSION,
        env=ENV_NAME,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    ), 200

@flask_app.route("/", methods=["HEAD"])
def _root_head():
    return ("", 204)

# Landing page kept at /landing for admin checks (optional bookmark)
@flask_app.get("/landing")
def landing_page():
    return render_template("landing.html",
        version=APP_VERSION,
        env=ENV_NAME,
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    ), 200

@flask_app.get("/robots.txt")
def robots_txt():
    resp = make_response("User-agent: *\nDisallow:\n", 200)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    return resp

@flask_app.get("/favicon.ico")
def favicon():
    from base64 import b64decode
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/avJkqkAAAAASUVORK5CYII=")
    resp = make_response(png, 200)
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@flask_app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify(error="not_found", path=request.path), 404
    return render_template("404.html", path=request.path), 404

@flask_app.errorhandler(405)
def handle_405(e):
    if request.path.startswith("/api/"):
        return jsonify(error="method_not_allowed", path=request.path), 405
    return render_template("405.html", path=request.path), 405

# --- WS ---
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

asgi.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
