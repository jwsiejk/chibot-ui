from __future__ import annotations

import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template, make_response, send_from_directory
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

# Resolve repo root, then point Flask to the REAL /static and /templates at repo root
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

ENV_NAME = os.environ.get("ENV_NAME", "prod")
APP_VERSION = os.environ.get("APP_VERSION", "v1")

flask_app = Flask(
    __name__,
    static_folder=STATIC_DIR,       # serve /static from repo root
    static_url_path="/static",
    template_folder=TEMPLATES_DIR,  # use existing templates if present
)

# Security headers (kept), allow inline/eval to match current bundle behavior
@flask_app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    )
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
    return resp

# v1 health (unchanged behavior)
try:
    from app.api_v1.health import bp as health_bp
    flask_app.register_blueprint(health_bp)
except Exception:
    # If the module isn't present in your tree yet, keep running; UI will still load.
    pass

# === UI entry ===
# Serve your ORIGINAL SPA entry. If templates/index.html exists, render it.
# Else if static/index.html exists, serve that. Otherwise 404 with guidance.
@flask_app.get("/")
def ui_root():
    tpl_index = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(tpl_index):
        return render_template("index.html", env=ENV_NAME, version=APP_VERSION,
                               now=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")), 200
    static_index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(static_index):
        return send_from_directory(STATIC_DIR, "index.html")
    return ("Missing UI entry. Expected templates/index.html or static/index.html.", 404)

@flask_app.route("/", methods=["HEAD"])
def _root_head():
    return ("", 204)

# robots + favicon helpers
@flask_app.get("/robots.txt")
def robots_txt():
    return ("User-agent: *\nDisallow:\n", 200, {"Content-Type":"text/plain; charset=utf-8"})

@flask_app.get("/favicon.ico")
def favicon():
    from base64 import b64decode
    png = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/avJkqkAAAAASUVORK5CYII=")
    return (png, 200, {"Content-Type":"image/png", "Cache-Control":"public, max-age=86400"})

# WebSocket passthrough (kept minimal; your real WS logic remains in your modules)
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
