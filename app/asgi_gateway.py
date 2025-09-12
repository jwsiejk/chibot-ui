# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI)
#   • All HTTP via mounted Flask WSGI app

import asyncio, time, json, os
from app import create_app
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute, Mount
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from starlette.websockets import WebSocket, WebSocketDisconnect
from app.ws.protocol import dumps, PROTO_ID, DEFAULT_HEARTBEAT_MS
from app.ws.bus import bus

# Optional admin log emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    def _admin_emit(*a, **k): pass

# Build the Flask WSGI app
flask_app = create_app()

# ---------- Legacy Diagnostics → Admin Diagnostics (unification) ----------
try:
    from flask import redirect
    @flask_app.get("/diagnostics")
    def _legacy_diag_redirect():       return redirect("/admin?tab=diag", code=302)
    @flask_app.get("/diagnostics/")
    def _legacy_diag_redirect_slash(): return redirect("/admin?tab=diag", code=302)
    @flask_app.get("/diagnostics.html")
    def _legacy_diag_redirect_html():  return redirect("/admin?tab=diag", code=302)
    @flask_app.get("/diagnostics/index")
    def _legacy_diag_redirect_idx():   return redirect("/admin?tab=diag", code=302)
except Exception:
    pass
# -------------------------------------------------------------------------

def _normalize_frame(fr: dict) -> dict:
    try:
        t = fr.get("type")
        if t == "text":
            return {"type": "assistant_chunk", "text": fr.get("content","")}
        if t == "end":
            out = {"type": "assistant_end"}
            if "finish_reason" in fr:
                out["finish_reason"] = fr["finish_reason"]
            return out
        return fr
    except Exception:
        return {"type":"error","message":"frame_normalization_failed"}

async def _keepalive_task(websocket: WebSocket, heartbeat_ms: int):
    try:
        while True:
            await asyncio.sleep(max(heartbeat_ms, 1000) / 1000.0)
            try:
                await websocket.send_text(dumps({"type":"keepalive","ts": int(time.time()*1000)}))
            except Exception:
                return
    except asyncio.CancelledError:
        return

# --- WebSocket endpoint ---
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    session_id = websocket.query_params.get("session_id", "") or "default"
    tab_id = websocket.query_params.get("tab") or websocket.query_params.get("tab_id") or "default"

    try:
        _admin_emit("ws_open", session_id=session_id, proto=PROTO_ID, tab_id=tab_id)
    except Exception:
        pass

    ready = {"type":"ready","session_id":session_id,"proto":PROTO_ID,"heartbeat_ms":DEFAULT_HEARTBEAT_MS,"ts": int(time.time()*1000)}
    await websocket.send_text(dumps(ready))

    loop = asyncio.get_running_loop()
    q = bus.subscribe(session_id)
    try:
        _admin_emit('ws_subscribed', session_id=session_id)
    except Exception:
        pass

    async def forward_bus():
        while True:
            try:
                frame = await loop.run_in_executor(None, q.get)
            except Exception:
                return
            try:
                await websocket.send_text(dumps(_normalize_frame(frame)))
            except Exception:
                return

    forward_task = asyncio.create_task(forward_bus())
    ka = asyncio.create_task(_keepalive_task(websocket, DEFAULT_HEARTBEAT_MS))

    try:
        while True:
            try:
                msg = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            if isinstance(msg, str) and msg.strip().lower() == "ping":
                try:
                    await websocket.send_text("pong")
                    _admin_emit("ws_pong", session_id=session_id)
                except Exception:
                    pass
                continue

            try:
                payload = json.loads(msg)
            except Exception:
                payload = None

            if isinstance(payload, dict):
                t = str(payload.get("type","")).lower()

                if t in ("ping", "keepalive"):
                    await websocket.send_text(dumps({"type":"pong","echo": payload.get("ts"), "ts": int(time.time()*1000)}))
                    continue

                if t == "interrupt":
                    await websocket.send_text(dumps({"type":"interrupt_ack","ts": int(time.time()*1000)}))
                    continue

                if t == "close":
                    break

            try:
                _admin_emit("ws_in", session_id=session_id, payload=(msg[:256] if isinstance(msg,str) else "<binary>"))
            except Exception:
                pass

    finally:
        for task in (ka, forward_task):
            try:
                task.cancel()
            except Exception:
                pass
        try:
            _admin_emit("ws_close", session_id=session_id)
        except Exception:
            pass
        try:
            await websocket.close(code=1000)
        except Exception:
            pass

# --- Compose Starlette app ---
routes = [
    WebSocketRoute("/ws/v1/chat", chat_ws),
    Mount("/", app=WSGIMiddleware(flask_app)),
]

_cors_origins = []
_allow = os.environ.get("CORS_ALLOWLIST","").strip()
if _allow:
    _cors_origins = [o.strip() for o in _allow.split(",") if o.strip()]

middleware = [Middleware(GZipMiddleware, minimum_size=1024)]
if _cors_origins:
    middleware.insert(0, Middleware(CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET","POST","OPTIONS"],
        allow_headers=["Content-Type","X-CSRF-Token"],
        allow_credentials=True))

asgi = Starlette(routes=routes, middleware=middleware)

# --- Idempotent blueprint registration to avoid double-register crashes ---
def _register_bp_once(name: str, bp):
    if name not in flask_app.blueprints:
        flask_app.register_blueprint(bp)

from app.api_v1.voice_mode import bp as voice_mode_bp
_register_bp_once("voice_mode_v1", voice_mode_bp)

from app.api_v1.voice_stream import bp as voice_stream_bp
_register_bp_once("voice_stream_v1", voice_stream_bp)

# Admin Diagnostics
try:
    from app.api_v1.admin_diagnostics import bp as admin_diag_bp
    _register_bp_once("admin_diag_v1", admin_diag_bp)
except Exception:
    pass

# --- Graceful shutdown: stop streaming manager loop/thread on SIGTERM ---
try:
    from app.services.streaming_asr.stream_manager import shutdown_manager
    async def _shutdown_streaming():
        try:
            await shutdown_manager()
        except Exception:
            pass
    asgi.add_event_handler("shutdown", _shutdown_streaming)
except Exception:
    pass
