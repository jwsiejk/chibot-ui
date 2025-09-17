
from flask import Response, request
import json, time
from queue import Empty
from ..ws.bus import bus

def register_ws_route(app):
    @app.route('/ws/v1/chat', methods=['GET'])
    def ws_sse_stream():
        sid = request.args.get('session_id') or 'default'
        q = bus.subscribe(sid)
        def stream():
            last_hb = 0.0
            sent_any = False
            last_send = time.time()
            # Initial ready state
            yield "data: " + json.dumps({"type":"state","phase":"ready","session_id":sid}) + "\n\n"
            while True:
                # Heartbeat every ~5s
                now = time.time()
                if now - last_hb > 5.0:
                    yield "event: heartbeat\n"
                    yield "data: " + json.dumps({"ts": now, "kind": "heartbeat", "msg": "ok"}) + "\n\n"
                    yield "data: " + json.dumps({"ts": now, "kind": "ping"}) + "\n\n"
                    last_hb = now
                try:
                    fr = q.get(timeout=0.25)
                except Empty:
                    # If we have sent any frames and idle for >1.8s, close stream
                    if sent_any and (time.time() - last_send) > 1.8:
                        break
                    continue
                # Normalize some frames to SSE
                t = fr.get("type")
                if t == "assistant_end":
                    # also push ready after assistant_end for UX/tests
                    yield "data: " + json.dumps(fr) + "\n\n"
                    sent_any = True
                    last_send = time.time()
                    sent_any = True
                    last_send = time.time()
                    yield "data: " + json.dumps({"type":"state","phase":"ready","session_id":sid}) + "\n\n"
                    sent_any = True
                    last_send = time.time()
                    sent_any = True
                    last_send = time.time()
                else:
                    yield "data: " + json.dumps(fr) + "\n\n"
                    sent_any = True
                    last_send = time.time()
        return Response(stream(), mimetype="text/event-stream")
