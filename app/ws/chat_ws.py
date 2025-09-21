# app/ws/chat_ws.py — SSE bridge for /ws/v1/chat (legacy stream)
# Notes:
# • Sends initial "ready" plus state:ready for UI compatibility
# • Heartbeat every ~5s (event: heartbeat + ping line)
# • KeepAliveAck every ~4s so clients see activity
# • Pushes state:ready after assistant_end
# • Closes after ~1.8s idle once any frames have been sent (kept from original)

from flask import Response, request, stream_with_context
import json, time
from queue import Empty
from ..ws.bus import bus

def _sse_event(data: dict, event: str | None = None):
    """Yield a single SSE event with optional event name."""
    if event:
        yield f"event: {event}\n"
    # compact JSON to reduce buffering
    yield "data: " + json.dumps(data, separators=(',', ':')) + "\n\n"

def register_ws_route(app):
    @app.route('/ws/v1/chat', methods=['GET'])
    def ws_sse_stream():
        sid = request.args.get('session_id') or 'default'
        q = bus.subscribe(sid)

        @stream_with_context
        def stream():
            last_hb = 0.0
            last_keep = 0.0
            sent_any = False
            last_send = time.time()

            # Initial connect signals (keep both for compatibility)
            yield from _sse_event({"type": "ready", "session_id": sid})
            yield from _sse_event({"type": "state", "phase": "ready", "session_id": sid})
            # Initial heartbeat/ping
            now = time.time()
            yield from _sse_event({"ts": now, "kind": "heartbeat", "msg": "ok"}, event="heartbeat")
            yield from _sse_event({"ts": now, "kind": "ping"})

            try:
                while True:
                    now = time.time()

                    # Heartbeat every ~5s (plus legacy ping line)
                    if now - last_hb > 5.0:
                        yield from _sse_event({"ts": now, "kind": "heartbeat", "msg": "ok"}, event="heartbeat")
                        yield from _sse_event({"ts": now, "kind": "ping"})
                        last_hb = now

                    # KeepAliveAck every ~4s so clients see activity
                    if now - last_keep > 4.0:
                        yield from _sse_event({"type": "KeepAliveAck"})
                        last_keep = now

                    # Drain bus with short timeout to allow heartbeats
                    try:
                        fr = q.get(timeout=0.25)
                    except Empty:
                        # If we have sent any frames and idle for >1.8s, close stream
                        if sent_any and (time.time() - last_send) > 1.8:
                            break
                        continue

                    # Forward the frame
                    t = fr.get("type")
                    yield from _sse_event(fr)
                    sent_any = True
                    last_send = time.time()

                    # After assistant_end, nudge UI back to ready
                    if t == "assistant_end":
                        yield from _sse_event({"type": "state", "phase": "ready", "session_id": sid})
                        sent_any = True
                        last_send = time.time()

            except GeneratorExit:
                # client disconnected
                pass
            except Exception:
                # swallow to keep SSE robust
                pass
            finally:
                try:
                    # Unsubscribe if bus provides an unsubscribe API
                    if hasattr(bus, "unsubscribe"):
                        bus.unsubscribe(sid, q)
                except Exception:
                    pass

        resp = Response(stream(), mimetype="text/event-stream")
        # Recommended headers for SSE behind proxies
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp
