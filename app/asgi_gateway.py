# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI)  -> bus bridge + ping/pong
#   • All HTTP via mounted Flask WSGI app

import asyncio, time, json
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

def _normalize_frame(fr: dict) -> dict:
    """
    Map internal bus frames -> client protocol:
      text(content)     -> assistant_chunk(text)
      end               -> assistant_end
      suggestions(list) -> suggestions (passthrough)
      audio_chunk       -> audio_chunk (passthrough)
    """
    try:
        t = fr.get("type")
        if t == "text":
            return {"type": "assistant_chunk", "text": fr.get("content","")}
        if t == "end":
            out = {"type": "assistant_end"}
            # include optional metadata
            if "finish_reason" in fr: out["finish_reason"] = fr["finish_reason"]
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
                # Connection likely closed
                return
    except asyncio.CancelledError:
        return

# --- WebSocket endpoint ---
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    session_id = websocket.query_params.get("session_id", "") or "default"
    tab_id = websocket.query_params.get("tab") or websocket.query_params.get("tab_id") or "default"

    # Announce open to admin log
    try:
        _admin_emit("ws_open", session_id=session_id, proto=PROTO_ID, tab_id=tab_id)
    except Exception:
        pass

    # Send 'ready' FIRST, deterministically encoded
    ready = {"type":"ready","session_id":session_id,"proto":PROTO_ID,"heartbeat_ms":DEFAULT_HEARTBEAT_MS,"ts": int(time.time()*1000)}
    await websocket.send_text(dumps(ready))

    # Subscribe to the bus and start forwarder
    loop = asyncio.get_running_loop()
    q = bus.subscribe(session_id)
        try:
            _emit('ws_subscribed', label='ws_subscribed', session_id=session_id)
        except Exception:
            pass
    async def forward_bus():
        from queue import Empty
        while True:
            try:
                frame = await loop.run_in_executor(None, q.get)
            except Exception:
                # Executor/bus error or closed
                return
            try:
                await websocket.send_text(dumps(_normalize_frame(frame)))
            except Exception:
                return
    forward_task = asyncio.create_task(forward_bus())

    # Start keepalive pings from server (optional; client may also ping)
    ka = asyncio.create_task(_keepalive_task(websocket, DEFAULT_HEARTBEAT_MS))

    try:
        while True:
            try:
                msg = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                # If receive fails, close out
                break

            # Handle plain text 'ping' for CI
            if isinstance(msg, str) and msg.strip().lower() == "ping":
                try:
                    await websocket.send_text("pong")
                    _admin_emit("ws_pong", session_id=session_id)
                except Exception:
                    pass
                continue

            # Try to parse JSON control frames
            try:
                payload = json.loads(msg)
            except Exception:
                payload = None

            if isinstance(payload, dict):
                t = str(payload.get("type","")).lower()

                if t == "ping":
                    # Application-level pong (JSON)
                    await websocket.send_text(dumps({"type":"pong","echo": payload.get("ts"), "ts": int(time.time()*1000)}))
                    continue

                if t == "interrupt":
                    # Signal: user barge-in; acknowledge (upstream cancellation handled elsewhere)
                    await websocket.send_text(dumps({"type":"interrupt_ack","ts": int(time.time()*1000)}))
                    continue

                if t == "close":
                    break

            # Unknown inbound -> ignore but log
            try:
                _admin_emit("ws_in", session_id=session_id, payload=(msg[:256] if isinstance(msg,str) else "<binary>"))
            except Exception:
                pass

    finally:
        # Teardown
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
import os as _os
_allow = _os.environ.get("CORS_ALLOWLIST","").strip()
if _allow:
    _cors_origins = [o.strip() for o in _allow.split(",") if o.strip()]

middleware = [Middleware(GZipMiddleware, minimum_size=1024)]
if _cors_origins:
    middleware.insert(0, Middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type","X-CSRF-Token"], allow_credentials=True))

asgi = Starlette(routes=routes, middleware=middleware)
