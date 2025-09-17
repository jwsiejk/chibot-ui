# app/ws/ws_asgi.py — Phase 1 (protocol only; vendor wiring in Phase 2)
from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any
from .schema_v1 import parse_client_json, make_keepalive_ack, make_results, make_utterance_end, make_error
from .turn_buffer import TurnBuffer

def _qparam(scope: dict, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        qs = (scope.get("query_string") or b"").decode("utf-8", "ignore")
        import urllib.parse as _p
        return (_p.parse_qs(qs).get(key) or [default])[0]
    except Exception:
        return default

async def ws_chat(scope, receive, send):
    if scope.get("type") != "websocket":
        # Not a websocket request — 404 response
        await send({"type":"http.response.start","status":404,"headers":[]})
        await send({"type":"http.response.body","body":b"not found"})
        return

    # Accept immediately; we don't negotiate subprotocol here
    await send({"type":"websocket.accept"})

    cfg: Dict[str, Any] = {}
    buf = TurnBuffer()

    try:
        while True:
            ev = await receive()
            et = ev.get("type")

            if et == "websocket.receive":
                # Binary audio frame
                if ev.get("bytes") is not None:
                    buf.append(ev.get("bytes") or b"")
                    continue
                # Text control frame
                if ev.get("text") is not None:
                    try:
                        obj = parse_client_json(ev.get("text") or "")
                        t = obj.get("type")
                        if t == "KeepAlive":
                            await send({"type":"websocket.send","text":__dumps(make_keepalive_ack())})
                        elif t == "Configure":
                            cfg.update(obj)  # record for Phase 2
                            # Optional ack could be added in later phases
                        elif t == "CloseStream":
                            turn_id, _pcm = buf.close_turn()
                            # Phase 1: we don't decode audio yet — emit an empty final
                            await send({"type":"websocket.send","text":__dumps(make_results(turn_id, transcript=""))})
                            await send({"type":"websocket.send","text":__dumps(make_utterance_end(turn_id))})
                    except ValueError as e:
                        await send({"type":"websocket.send","text":__dumps(make_error("bad_message", str(e)))})

            elif et == "websocket.disconnect":
                break
            else:
                # ignore other event types
                pass
    finally:
        try:
            await send({"type":"websocket.close"})
        except Exception:
            pass

# Local compact JSON (avoid orjson reliance in tests)
def __dumps(obj) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
