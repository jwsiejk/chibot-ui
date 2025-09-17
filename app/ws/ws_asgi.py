# app/ws/ws_asgi.py — Phase 2 (Deepgram wired; pass-through Results)
from __future__ import annotations
import asyncio, os, contextlib
from typing import Optional, Dict, Any

from .schema_v1 import parse_client_json, make_keepalive_ack, make_results, make_utterance_end, make_error
from .turn_buffer import TurnBuffer
from app.services.streaming_asr.deepgram_client import DeepgramClient
from app.security.ws_token import verify as verify_ws_token

def _dumps(obj) -> str:
    import json as _json
    return _json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def _has_deepgram_key() -> bool:
    return bool((os.getenv("DEEPGRAM_API_KEY") or "").strip())

async def _pump_dg_to_client(dg: DeepgramClient, send, turn_id_ref, final_seen):
    """Relay Deepgram events to client as Results/UtteranceEnd."""
    try:
        async for ev in dg.events():
            et = (ev.get("type") or "").lower()
            if et == "asr_open":
                # ignore
                continue
            if et in ("user_partial","user_final"):
                is_final = (et == "user_final")
                text = ev.get("text") or ""
                await send({"type":"websocket.send","text": _dumps(make_results(turn_id_ref[0], transcript=text, confidence=0.0, is_final=is_final))})
                if is_final:
                    final_seen[0] = True
                    await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id_ref[0]))})
            elif et == "asr_error":
                await send({"type":"websocket.send","text": _dumps(make_error("asr_error", str(ev.get("error") or "unknown")))})
            # else: ignore unknown
    except asyncio.CancelledError:
        return
    except Exception as e:
        # Non-fatal in tests without vendor
        try:
            await send({"type":"websocket.send","text": _dumps(make_error("relay_fail", e.__class__.__name__))})
        except Exception:
            pass

async def _ws_chat_asgi_impl(scope, receive, send):
    if scope.get("type") != "websocket":
        await send({"type":"http.response.start","status":404,"headers":[]})
        await send({"type":"http.response.body","body":b"not found"})
        return

    await send({"type":"websocket.accept"})

    cfg: Dict[str, Any] = {}
    buf = TurnBuffer()
    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None
    turn_id_ref = [0]  # box for closure
    final_seen = [False]

    async def _ensure_dg_connected():
        nonlocal dg, rx_task
        if dg is None and _has_deepgram_key():
            dg = DeepgramClient(cfg)
            await dg.connect()  # dg.connect
            # set current turn id
            turn_id_ref[0] = buf.turn_seq + 1
            rx_task = asyncio.create_task(_pump_dg_to_client(dg, send, turn_id_ref, final_seen))

    # ws_token_checked
    require_token = (os.getenv("WS_TOKEN_REQUIRED","1").lower() not in ("0","false","no"))
    token = None
    try:
        hdrs = {k.decode().lower(): v.decode() for k,v in (scope.get('headers') or [])}
        if 'authorization' in hdrs and hdrs['authorization'].lower().startswith('bearer '):
            token = hdrs['authorization'].split(' ',1)[1].strip()
    except Exception:
        pass
    if not token and scope.get('query_string'):
        try:
            q = dict([tuple(p.split('=',1)) for p in scope.get('query_string').decode().split('&') if '=' in p])
            token = q.get('ws_token') or token
        except Exception:
            pass
    if require_token:
        try:
            _payload = verify_ws_token(token or "")
        except Exception:
            await send({'type':'websocket.close','code':4401})
            return
    else:
        if token:
            try: _payload = verify_ws_token(token)
            except Exception: pass
    try:
        while True:
            ev = await receive()
            et = ev.get("type")
        
            if et == "websocket.receive":
                # Binary frames → forward to Deepgram when enabled; always buffer locally
                if ev.get("bytes") is not None:
                    chunk = ev.get("bytes") or b""
                    buf.append(chunk)
                    if _has_deepgram_key():
                        await _ensure_dg_connected()
                        if dg is not None:
                            await dg.send(chunk)  # dg.send
                    continue
        
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")
                        if t == "KeepAlive":
                            await send({"type":"websocket.send","text": _dumps(make_keepalive_ack())})
                        elif t == "Configure":
                            cfg.update(obj)
                            # DeepgramClient sends its own Configure on connect; future phases can map fields.
                        elif t == "CloseStream":
                            # Close the current turn.
                            turn_id, _pcm = buf.close_turn()
                            turn_id_ref[0] = turn_id
                            if _has_deepgram_key() and dg is not None:
                                await dg.close(wait_for_final=True)  # dg.close
                                # rx_task will emit final + utterance_end
                                if not final_seen[0]:
                                    # Provider did not return a final; emit a minimal final to satisfy contract
                                    await send({"type":"websocket.send","text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                    await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id))})
                                    await send({"type":"websocket.send","text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                    await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id))})
                            else:
                                # No vendor: emit an empty final to satisfy contract
                                await send({"type":"websocket.send","text": _dumps(make_results(turn_id, transcript="", is_final=True))})
                                await send({"type":"websocket.send","text": _dumps(make_utterance_end(turn_id))})
                    except ValueError as e:
                        await send({"type":"websocket.send","text": _dumps(make_error("bad_message", str(e)))})
        
            elif et == "websocket.disconnect":
                break
            else:
                # ignore
                pass
    finally:
        try:
            if dg is not None:
                try:
                    await dg.close(wait_for_final=False)
                except Exception:
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
                await send({"type":"websocket.close"})
            except Exception:
                pass


# --- Compatibility wrapper ---
# Supports both:
#   • ASGI style: ws_chat(scope, receive, send)
#   • Starlette style: ws_chat(websocket)
try:
    # Starlette's WebSocket class (only used if available)
    from starlette.websockets import WebSocket as _StarletteWebSocket
except Exception:  # pragma: no cover
    _StarletteWebSocket = None

async def ws_chat(websocket_or_scope, receive=None, send=None):
    # If called Starlette-style, we'll be given a single WebSocket object.
    if receive is None and send is None:
        websocket = websocket_or_scope
        # Guard: if the import isn't available or object doesn't look like Starlette's WebSocket,
        # fall back to treating it as ASGI scope (unlikely in production).
        if _StarletteWebSocket is None or not hasattr(websocket, "receive"):
            # Treat the single arg as scope (ASGI) and expect external receive/send (cannot proceed cleanly)
            raise TypeError("ws_chat(websocket) called but no Starlette WebSocket available")
        
        async def _receive():
            # Starlette returns dicts with 'type' ('websocket.receive'/'websocket.disconnect')
            # and 'text' or 'bytes' for payloads. This matches what our ASGI impl expects.
            return await websocket.receive()
        
        async def _send(event: dict):
            et = event.get("type")
            if et == "websocket.accept":
                # Accept with defaults
                await websocket.accept()
            elif et == "websocket.send":
                if "text" in event and event["text"] is not None:
                    await websocket.send_text(event["text"])
                elif "bytes" in event and event["bytes"] is not None:
                    await websocket.send_bytes(event["bytes"])
                else:
                    # No payload; send empty text (harmless)
                    await websocket.send_text("")
            elif et == "websocket.close":
                code = event.get("code", 1000)
                await websocket.close(code=code)
            else:
                # ignore other event types
                pass
        
        # Delegate to the original ASGI implementation using the adapters
        return await _ws_chat_asgi_impl(websocket.scope, _receive, _send)
    
    # Otherwise treat it as raw ASGI 3-callable.
    return await _ws_chat_asgi_impl(websocket_or_scope, receive, send)
