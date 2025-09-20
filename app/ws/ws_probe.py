# app/ws/ws_probe.py
import json
async def ws_probe(scope, receive, send):
    if scope.get("type") != "websocket":
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})
        return
    try:
        await send({"type": "websocket.accept", "subprotocol": "probe"})
        await send({"type": "websocket.send", "text": json.dumps({"type":"probe_ready"})})
    except Exception:
        pass
    try:
        await send({"type": "websocket.close", "code": 1000, "reason": "probe_done"})
    except Exception:
        pass
