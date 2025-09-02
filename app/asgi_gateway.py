from __future__ import annotations

import os
import json
import asyncio
from datetime import datetime as _dt
from typing import Any, Dict

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.websockets import WebSocket
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from asgiref.wsgi import WsgiToAsgi

# Import your existing Flask app factory (keeps /, /api/me, /api/features, templates, static)
from app.legacy_app import create_app as create_flask_app  # type: ignore


# -----------------------------
# Helpers
# -----------------------------
def _bool_env(name: str, default: bool = False) -> bool:
    truthy = {"1", "true", "yes", "on"}
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in truthy


def json_error(status: int, error: str, detail: str = "") -> JSONResponse:
    payload: Dict[str, Any] = {"ok": False, "error": error, "status": status}
    if detail:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=status)


# -----------------------------
# HTTP endpoints (v1 contract)
# -----------------------------
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "time": _dt.utcnow().isoformat() + "Z"})

async def features_v1(request: Request) -> JSONResponse:
    # If you want features from Flask, keep calling /api/features from the UI.
    # This v1 variant is provided for symmetry if you want to migrate the UI later.
    data = {
        "AUDIO": _bool_env("FEATURE_AUDIO", True),
        "HISTORY": _bool_env("FEATURE_HISTORY", False),
        "ADMIN_UI": _bool_env("FEATURE_ADMIN_UI", False),
        "TOOLS": _bool_env("FEATURE_TOOLS", False),
        "EMAIL": _bool_env("FEATURE_EMAIL", True),
        "ACCOUNTS": _bool_env("FEATURE_ACCOUNTS", False),
    }
    return JSONResponse({"ok": True, "features": data})

async def chat_v1(request: Request) -> JSONResponse:
    payload = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    message = (payload.get("message") or "").strip()
    session_id = payload.get("session_id") or ""
    if not message:
        return json_error(400, "Bad Request", "message is required")
    # Placeholder for your LLM/orchestrator
    return json_error(501, "Not Implemented", "chat orchestration not wired yet")

async def tts_v1(request: Request) -> JSONResponse:
    payload = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    text = (payload.get("text") or "").strip()
    if not text:
        return json_error(400, "Bad Request", "text is required")
    # Placeholder for TTS adapter (ElevenLabs, etc.)
    return json_error(501, "Not Implemented", "TTS adapter not wired yet")

async def stt_v1(request: Request) -> JSONResponse:
    # Placeholder for STT adapter
    return json_error(501, "Not Implemented", "STT adapter not wired yet")

# SSE admin logs
async def admin_logs(request: Request) -> StreamingResponse:
    async def event_stream():
        yield b"retry: 2000\n\n"
        while True:
            now = str(int(asyncio.get_event_loop().time()))
            data = f"event: ping\ndata: {now}\n\n".encode("utf-8")
            yield data
            await asyncio.sleep(10)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


# -----------------------------
# WebSocket endpoint
# -----------------------------
async def ws_chat(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "ready"})
    try:
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
            except Exception:
                await websocket.send_json({"type": "error", "detail": "invalid_json"})
                continue
            mtype = data.get("type")
            if mtype == "start":
                await websocket.send_json({"type": "ai_text", "text": "(stub) voice pipeline not wired"})
            elif mtype == "end":
                await websocket.send_json({"type": "done"})
                await websocket.close()
                break
            elif mtype in ("user_audio", "user_text"):
                await websocket.send_json({"type": "ai_text", "text": "(stub)"})
            else:
                await websocket.send_json({"type": "notice", "echo": data})
    except Exception:
        # connection closed or error; just exit
        return


# -----------------------------
# Assemble ASGI app
# -----------------------------
flask_app = create_flask_app()
wsgi_mounted = WsgiToAsgi(flask_app)

routes = [
    Route("/healthz", healthz, methods=["GET"]),
    # v1 API
    Route("/api/v1/features", features_v1, methods=["GET"]),
    Route("/api/v1/chat", chat_v1, methods=["POST"]),
    Route("/api/v1/voice/tts-with-visemes", tts_v1, methods=["POST"]),
    Route("/api/v1/voice/stt", stt_v1, methods=["POST"]),
    Route("/api/v1/admin/logs", admin_logs, methods=["GET"]),
    # WebSocket
    WebSocketRoute("/ws/v1/chat", ws_chat),
    # Mount the existing Flask app for everything else (/, /api/me, /api/features, static, templates)
    Mount("/", app=wsgi_mounted),
]

asgi = Starlette(routes=routes)

# CORS scoped to API/WS
allow_origin = os.getenv("CORS_ALLOW_ORIGIN", "*")
asgi.add_middleware(
    CORSMiddleware,
    allow_origins=[allow_origin] if allow_origin != "*" else ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Error handler for uniform JSON where relevant
@asgi.exception_handler(Exception)
async def on_error(request, exc: Exception):
    # For API/WS paths, respond with JSON
    if request.url.path.startswith(("/api/", "/ws/")):
        status = getattr(exc, "status_code", 500)
        return json_error(status if isinstance(status, int) else 500, "server_error", str(exc))
    return JSONResponse({"ok": False, "error": "server_error"}, status_code=500)
