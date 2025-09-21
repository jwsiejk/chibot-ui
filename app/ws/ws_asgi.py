# app/ws/ws_asgi.py — Phase 2 (Deepgram wired; pass-through Results)
from __future__ import annotations
import asyncio, os, contextlib, time
from typing import Optional, Dict, Any

from .schema_v1 import parse_client_json, make_keepalive_ack, make_results, make_utterance_end, make_error
from .turn_buffer import TurnBuffer
from app.services.streaming_asr.deepgram_client import DeepgramClient
from app.security.ws_token import verify as verify_ws_token
from app.db import db
from app.metrics import ws_metrics

# Optional admin emitter
try:
    from app.api_v1.admin import _emit as _admin_emit
except Exception:
    _admin_emit = None


# ----------------------------- helpers -----------------------------

def _client_ip_from_scope(scope) -> str:
    try:
        c = scope.get("client") or ()
        if isinstance(c, (list, tuple)) and c:
            return str(c[0])
    except Exception:
        pass
    try:
        hdrs = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        xff = hdrs.get("x-forwarded-for","").split(",")[0].strip()
        if xff:
            return xff
    except Exception:
        pass
    return "unknown"

def _jlog(event: str, **fields):
    try:
        import time as _t, json as _json
        fields.setdefault("event", event)
        fields.setdefault("ts", _t.time())
        print(_json.dumps(fields, separators=(",",":"), ensure_ascii=False))
    except Exception:
        pass

def _dumps(obj) -> str:
    import json as _json
    return _json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def _get_session_id(scope) -> str:
    try:
        raw = (scope.get("query_string") or b"").decode("utf-8", "ignore")
        if raw:
            for pair in raw.split("&"):
                if not pair or "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                if k == "session_id":
                    return v or "default"
    except Exception:
        pass
    return "default"

def _has_deepgram_key() -> bool:
    return bool((os.getenv("DEEPGRAM_API_KEY") or "").strip())


async def _pump_bus_to_client(sid: str, send):
    """Forward frames from StreamBus to the WS client as JSON."""
    import json as _json
    from queue import Empty
    from app.ws.bus import bus  # import here to tolerate file layout variants
    q = bus.subscribe(sid)
    while True:
        try:
            fr = q.get(timeout=0.05)
        except Empty:
            await asyncio.sleep(0.01)
            continue
        try:
            await send({"type": "websocket.send", "text": _json.dumps(fr, separators=(",", ":"), ensure_ascii=False)})
        except Exception:
            # Continue pumping; client may be transiently busy
            await asyncio.sleep(0.01)


async def _pump_dg_to_client(dg: DeepgramClient, send, turn_id_ref, final_seen):
    """Relay Deepgram events to client as Results/UtteranceEnd."""
    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                continue
            if et in ("user_partial", "user_final"):
                is_final = (et == "user_final")
                text = ev.get("text") or ""
                await send({"type": "websocket.send",
                            "text": _dumps(make_results(turn_id_ref[0], transcript=text, confidence=0.0, is_final=is_final))})
                if is_final:
                    final_seen[0] = True
                    await send({"type": "websocket.send", "text": _dumps(make_utterance_end(turn_id_ref[0]))})
            elif et == "asr_error":
                await send({"type": "websocket.send",
                            "text": _dumps(make_error("asr_error", str(ev.get("error") or "unknown")))})
    except asyncio.CancelledError:
        return
    except Exception as e:
        try:
            await send({"type": "websocket.send",
                        "text": _dumps(make_error("relay_fail", e.__class__.__name__))})
        except Exception:
            pass


# --------------------------- main ASGI app --------------------------

async def _ws_chat_asgi_impl(scope, receive, send):
    # Breadcrumb: did we enter the handler?
    try:
        if _admin_emit:
            _admin_emit("ws_handshake_enter",
                        path=scope.get("path"),
                        raw_query=(scope.get("query_string") or b"").decode("utf-8","ignore"))
    except Exception:
        pass

    if scope.get("type") != "websocket":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})
        return

    # Compute sid early so logs/auth can reference it safely
    sid = _get_session_id(scope)

    # === Pre-accept auth (subprotocols preferred) ===
    require_token = os.getenv("WS_TOKEN_REQUIRED", "1").lower() not in ("0","false","no")
    bearer_only   = os.getenv("WS_BEARER_ONLY", "1").lower() not in ("0","false","no")
    fail_limit = int(os.getenv("WS_FAIL_LIMIT","10"))
    fail_window_sec = float(os.getenv("WS_FAIL_WINDOW_SEC","60"))
    client_ip = _client_ip_from_scope(scope)

    token = None
    # 1) Subprotocols: ['bearer', 'bearer.<JWT>']
    try:
        for _sp in (scope.get("subprotocols") or []):
            if isinstance(_sp, str) and _sp.startswith("bearer."):
                token = _sp.split(".", 1)[1].strip()
                break
    except Exception:
        pass

    # 2) Authorization: Bearer <token> (non-browser clients)
    if not token:
        try:
            hdrs = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
            if "authorization" in hdrs and hdrs["authorization"].lower().startswith("bearer "):
                token = hdrs["authorization"].split(" ", 1)[1].strip()
        except Exception:
            pass

    # 3) Optional query fallback only if WS_BEARER_ONLY=0
    if (not bearer_only) and (not token) and scope.get("query_string"):
        try:
            q = dict([tuple(p.split("=", 1)) for p in scope.get("query_string").decode().split("&") if "=" in p])
            token = q.get("ws_token") or token
        except Exception:
            pass

    if require_token:
        try:
            _ = verify_ws_token(token or "")
        except Exception:
            over = ws_metrics.record_fail(client_ip, fail_limit, fail_window_sec)
            _jlog("ws_auth_fail", ip=client_ip, sid=sid, over_limit=over, via="preaccept")
            if _admin_emit:
                try:
                    _admin_emit("ws_auth_fail", sid=sid, ip=client_ip, over_limit=over)
                except Exception:
                    pass
            try:
                await send({"type": "websocket.close", "code": 4401})
            except Exception:
                pass
            return
    # === End pre-accept auth ===

    # Accept with subprotocol 'bearer' so JWT isn't echoed
    await send({"type": "websocket.accept", "subprotocol": "bearer"})

    # Clear greet state (if present), then create pumps
    try:
        db.memory.setdefault("greet_turns", {}).pop(sid, None)
    except Exception:
        pass

    bus_task = asyncio.create_task(_pump_bus_to_client(sid, send))

    # Optional server→client keepalive (every 20s)
    async def _ping_loop():
        try:
            while True:
                await asyncio.sleep(20)
                try:
                    await send({"type": "websocket.send",
                                "text": _dumps({"type":"keepalive", "ts": int(time.time()*1000)})})
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
    ping_task = asyncio.create_task(_ping_loop())

    # Initial READY
    try:
        await send({"type": "websocket.send", "text": _dumps({"type": "ready", "session_id": sid})})
    except Exception:
        # If we can't even send ready, close fast (should be rare)
        try:
            await send({"type": "websocket.close", "code": 1011, "reason": "initial_ready_failed"})
        except Exception:
            pass
        return

    # Runtime state
    cfg: Dict[str, Any] = {}
    buf = TurnBuffer()
    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None
    turn_id_ref = [0]
    final_seen = [False]

    async def _ensure_dg_connected():
        nonlocal dg, rx_task
        if dg is None and _has_deepgram_key():
            dg = DeepgramClient(cfg)
            await dg.connect()
            turn_id_ref[0] = buf.turn_seq + 1
            rx_task = asyncio.create_task(_pump_dg_to_client(dg, send, turn_id_ref, final_seen))

    # ---------------------- MAIN RECEIVE LOOP ----------------------
    try:
        while True:
            ev = await receive()
            et = ev.get("type")

            if et == "websocket.disconnect":
                break

            if et == "websocket.receive":
                # Binary/audio lane
                if ev.get("bytes") is not None:
                    chunk = ev.get("bytes") or b""
                    buf.append(chunk)
                    if _has_deepgram_key():
                        await _ensure_dg_connected()
                        if dg is not None:
                            await dg.send(chunk)
                    continue

                # Text/control lane
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")
                        if t == "KeepAlive":
                            await send({"type": "websocket.send", "text": _dumps(make_keepalive_ack())})

                        elif t == "Configure":
                            cfg.update(obj or {})

                            # Greet handling (unchanged from your last patch)
                            if obj.get("reset"):
                                try:
                                    db.memory.setdefault("greet_turns", {}).pop(sid, None)
                                except Exception:
                                    pass
                                try:
                                    if _admin_emit:
                                        _admin_emit("greet:reset", route="/ws/v1/chat", label="greet:reset", session_id=sid)
                                except Exception:
                                    pass

                            if obj.get("greet"):
                                async def _bg():
                                    try:
                                        from app.services.streaming import make_assistant_frames, schedule_frames
                                        from app.services.suggestions import hygienic_suggestions
                                        from app.ws.bus import bus
                                        try:
                                            tid, frames = make_assistant_frames("greet", sid, meta={"source":"ws_greet"})
                                        except Exception:
                                            fallback_text = "Hello! I'm Chip. How can I help today?"
                                            tid, frames = make_assistant_frames(fallback_text, sid, meta={"source":"ws_greet_fallback"})
                                        try:
                                            schedule_frames(sid, frames)
                                        except Exception:
                                            pass
                                        try:
                                            bus.broadcast(sid, {"type":"state","phase":"ready"})
                                            bus.broadcast(sid, {"type":"suggestions","turn_id":tid,"items":hygienic_suggestions("")})
                                        except Exception:
                                            pass
                                        try:
                                            if _admin_emit:
                                                cfg_now = db.get_config()
                                                audio_on = bool((cfg_now or {}).get("feature_audio", True))
                                                _admin_emit("greet:resp", label="greet:resp", session_id=sid, turn_id=tid, audio_scheduled=audio_on)
                                        except Exception:
                                            pass
                                    except Exception as e:
                                        try:
                                            await send({"type":"websocket.send","text":_dumps(make_error("greet_fail", e.__class__.__name__))})
                                        except Exception:
                                            pass
                                asyncio.create_task(_bg())

                        elif t == "User":
                            # New: handle user messages over WS
                            text = (obj.get("text") or "").strip()
                            if text:
                                try:
                                    from app.services.streaming import make_assistant_frames, schedule_frames
                                    tid, frames = make_assistant_frames(text, sid, meta={"source": "user_ws"})
                                    schedule_frames(sid, frames, correlation_user_msg_id=obj.get("userMsgId"))
                                except Exception as e:
                                    await send({
                                        "type":"websocket.send",
                                        "text":_dumps(make_error("user_fail", e.__class__.__name__))
                                    })

                        elif t == "CloseStream":
                            turn_id, _pcm = buf.close_turn()
                            turn_id_ref[0] = turn_id
                            if _has_deepgram_key() and dg is not None:
                                await dg.close(wait_for_final=True)
                                if not final_seen[0]:
                                    await send({"type": "websocket.send",
                                                "text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                    await send({"type": "websocket.send", "text": _dumps(make_utterance_end(turn_id))})
                            else:
                                await send({"type": "websocket.send",
                                            "text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                await send({"type": "websocket.send", "text": _dumps(make_utterance_end(turn_id))})
                        else:
                            pass
                    except ValueError as e:
                        await send({"type": "websocket.send", "text": _dumps(make_error("bad_message", str(e)))})
            else:
                pass

    finally:
        try:
            if rx_task:
                rx_task.cancel()
                with contextlib.suppress(Exception):
                    await rx_task
        except Exception:
            pass
        try:
            if dg is not None:
                with contextlib.suppress(Exception):
                    await dg.close(wait_for_final=False)
        except Exception:
            pass
        try:
            bus_task.cancel()
        except Exception:
            pass
        try:
            ping_task.cancel()
        except Exception:
            pass
        try:
            await send({"type":"websocket.close","code":1000,"reason":"normal_shutdown"})
        except Exception:
            pass


# --- Compatibility wrapper (not used by Starlette mount, kept for tests) ---
try:
    from starlette.websockets import WebSocket as _StarletteWebSocket  # noqa
except Exception:
    _StarletteWebSocket = None

async def ws_chat(websocket):
    """Accept, validate, send ready, then pump frames to keep the connection alive."""
    print(">>> REAL ws_chat from ws_asgi.py invoked <<<")
    await websocket.accept()
    try:
        sid = _get_session_id(websocket.scope)
    except Exception:
        sid = "default"

    try:
        await websocket.send_text(_dumps({"type":"ready","session_id":sid}))
    except Exception:
        try:
            await websocket.close(code=1011, reason="initial_ready_failed")
            await asyncio.sleep(0.05)
        finally:
            return

    try:
        await _pump_bus_to_client(sid, lambda msg: websocket.send_text(msg.get("text") or ""))
    except Exception:
        pass
    finally:
        try:
            await websocket.close(code=1000, reason="normal_shutdown")
            await asyncio.sleep(0.05)
        except Exception:
            pass
