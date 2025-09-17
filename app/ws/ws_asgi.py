
# app/ws/ws_asgi.py — Phase 2 (Deepgram wired; pass-through Results)
import json, asyncio, time
from typing import Optional
from .schema_v1 import parse_client_json, make_keepalive_ack
from app.services.streaming_asr.deepgram_client import DeepgramClient

def _qparam(scope: dict, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        qs = (scope.get("query_string") or b"").decode("utf-8", "ignore")
        import urllib.parse as _p
        return (_p.parse_qs(qs).get(key) or [default])[0]
    except Exception:
        return default

async def ws_chat(scope, receive, send):
    if scope.get("type") != "websocket":
        await send({"type":"http.response.start","status":404,"headers":[(b'content-type', b'text/plain')]})
        await send({"type":"http.response.body","body": b'not found'})
        return

    await send({"type":"websocket.accept"})
    session_id = _qparam(scope, "session_id", "default")

    dg: Optional[DeepgramClient] = None
    rx_task: Optional[asyncio.Task] = None

    async def ensure_connect():
        nonlocal dg, rx_task
        if dg is not None:
            return
        dg = DeepgramClient()
        await dg.connect()
        async def pump():
            async for ev in dg.events():
                try:
                    # Pass through Deepgram JSON messages we care about
                    # We forward only Results and UtteranceEnd to the client.
                    t = ev.get("type")
                    if t == "Results" or t == "UtteranceEnd":
                        await send({"type":"websocket.send","text": json.dumps(ev, separators=(",",":"))})
                except Exception:
                    # swallow to keep pump alive
                    pass
        rx_task = asyncio.create_task(pump())

    try:
        while True:
            event = await receive()
            etype = event.get("type")
            if etype == "websocket.receive":
                if event.get("bytes") is not None:
                    # Binary mic frame
                    if dg is None:
                        try:
                            await ensure_connect()
                        except Exception:
                            # Cannot connect ASR; close gracefully
                            await send({"type":"websocket.send","text": json.dumps({"type":"Error","error":"asr_connect_failed"}, separators=(",",":"))})
                            break
                    try:
                        await dg.send(event["bytes"])
                    except Exception:
                        # Ignore send errors to keep loop alive
                        pass
                elif event.get("text") is not None:
                    # Control JSON: KeepAlive or CloseStream
                    try:
                        msg = parse_client_json(event["text"])
                    except ValueError:
                        continue
                    mtype = msg["type"]
                    if mtype == "KeepAlive":
                        await send({"type":"websocket.send","text": json.dumps(make_keepalive_ack(), separators=(",",":"))})
                    elif mtype == "CloseStream":
                        if dg is not None:
                            try:
                                await dg.close(wait_for_final=True)
                            except Exception:
                                pass
                            # Create a new Deepgram stream on next binary
                            dg = None
                            if rx_task:
                                try: rx_task.cancel()
                                except Exception: pass
                                rx_task = None
                # else: ignore unknown
            elif etype == "websocket.disconnect":
                break
            else:
                # ignore other event types
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
                await send({"type":"websocket.close"})
            except Exception:
                pass
