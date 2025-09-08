# app/asgi_gateway.py
# Starlette ASGI app that serves:
#   • WebSocket /ws/v1/chat  (native ASGI)
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

# Optional admin log emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    def _admin_emit(*a, **k): pass

# Build the Flask WSGI app
flask_app = create_app()

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

    # Announce open to admin log
    try:
        _admin_emit("ws_open", session_id=session_id, proto=PROTO_ID)
    except Exception:
        pass

    # Subscribe to bus
    from app.ws.bus import bus as _bus
    q = _bus.subscribe(session_id)

    # Send ready frame immediately
    ready = {"type":"ready","session_id":session_id,"proto":PROTO_ID,"heartbeat_ms":DEFAULT_HEARTBEAT_MS}
    try:
        await websocket.send_text(dumps(ready))
    except Exception:
        pass

    stop = False

    async def forward_bus():
        from queue import Empty
        while not stop:
            try:
                fr = q.get(timeout=0.05)
            except Empty:
                await asyncio.sleep(0.01)
                continue
            # Normalize server-internal dialect to client dialect
            t = fr.get("type")
            if t == "text":
                fr = {"type":"assistant_chunk","turn_id":fr.get("turn_id"),"text":fr.get("content") or fr.get("text") or ""}
            elif t == "end":
                fr = {"type":"assistant_end","turn_id":fr.get("turn_id"),"suggestions":fr.get("suggestions") or []}
            try:
                await websocket.send_text(dumps(fr))
            except Exception:
                break

    # Heartbeat (ping) helper
    async def heartbeat():
        try:
            while not stop:
                await asyncio.sleep(DEFAULT_HEARTBEAT_MS/1000.0)
                try:
                    await websocket.send_text(dumps({"type":"ping","t":int(time.time()*1000)}))
                except Exception:
                    break
        except Exception:
            pass

    # Start tasks
    fwd_task = asyncio.create_task(forward_bus())
    hb_task  = asyncio.create_task(heartbeat())

    try:
        # Receive loop (only control frames presently)
        while True:
            event = await websocket.receive_text()
            try:
                msg = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                # If receive fails, close out
                break

            # Try to parse JSON control frames; allow plain 'ping' too
            payload = None
            if msg.strip().lower() == "ping":
                payload = {"type":"ping"}
            else:
                try:
                    payload = json.loads(msg)
                except Exception:
                    payload = None

            if isinstance(payload, dict):
                t = str(payload.get("type","")).lower()

                if t == "ping":
                    # Application-level pong
                    await websocket.send_text(dumps({"type":"pong","echo": payload.get("ts"), "ts": int(time.time()*1000)}))
                    continue

                if t == "interrupt":
                    # Signal: user barge-in; acknowledge
                    await websocket.send_text(dumps({"type":"interrupt_ack","ts": int(time.time()*1000)}))
                    # (Upstream cancel would go here in a full implementation)
                    continue

                if t == "close":
                    await websocket.close(code=1000)
                    break

            # Ignore other frames; this WS is primarily server-push
            # You can still observe inbound frames for debugging:
            try:
                _admin_emit("ws_in", session_id=session_id, payload=msg[:256])
            except Exception:
                pass

    finally:
        stop = True
        try:
            fwd_task.cancel()
        except Exception:
            pass
        try:
            hb_task.cancel()
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
