
# app/ws/ws_asgi.py — Phase 1 schema-compliant WS handler
import json, asyncio, time
from .schema_v1 import parse_client_json, make_results, make_utterance_end, make_keepalive_ack
from .turn_buffer import TurnBuffer

async def ws_chat(scope, receive, send):
    """
    ASGI WebSocket endpoint for /ws/v1/chat
    - Accepts binary frames as mic audio
    - Accepts JSON control frames: KeepAlive, CloseStream
    - On CloseStream, emits Results (empty transcript for Phase 1) and UtteranceEnd
    """
    if scope.get("type") != "websocket":
        await send({"type":"http.response.start","status":404,"headers":[(b'content-type', b'text/plain')]})
        await send({"type":"http.response.body","body": b'not found'})
        return
    await send({"type":"websocket.accept"})
    buf = TurnBuffer()

    try:
        while True:
            event = await receive()
            etype = event.get("type")
            if etype == "websocket.receive":
                if "bytes" in event and event["bytes"] is not None:
                    # Binary mic frame
                    buf.append(event["bytes"])
                elif "text" in event and event["text"] is not None:
                    # Control frame (JSON)
                    try:
                        msg = parse_client_json(event["text"])
                    except ValueError:
                        # ignore invalid control
                        continue
                    mtype = msg["type"]
                    if mtype == "KeepAlive":
                        await send({"type":"websocket.send","text": json.dumps(make_keepalive_ack(), separators=(",",":"))})
                    elif mtype == "CloseStream":
                        turn_id, _pcm = buf.close_turn()
                        # Phase 1: we don't call STT; emit minimal Results + UtteranceEnd
                        await send({"type":"websocket.send","text": json.dumps(make_results(turn_id, ""), separators=(",",":"))})
                        await send({"type":"websocket.send","text": json.dumps(make_utterance_end(turn_id), separators=(",",":"))})
                else:
                    # Unknown frame: ignore
                    pass
            elif etype == "websocket.disconnect":
                break
            else:
                # ignore other event types (e.g., lifespan)
                pass
    finally:
        try:
            await send({"type":"websocket.close"})
        except Exception:
            pass
