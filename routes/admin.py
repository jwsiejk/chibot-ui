from __future__ import annotations
from flask import Blueprint, jsonify, request, session, Response, stream_with_context
import time, json
from services.call_log import recent, clear, is_admin

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

def _guard():
    email = (session.get("email") or "").lower()
    if not is_admin(email):
        return False, jsonify({"ok": False, "error": "forbidden"}), 403
    return True, email, None

@admin_bp.get("/status")
def status():
    ok, payload, code = _guard()
    if not ok: return payload, code
    return jsonify({"ok": True, "is_admin": True})

@admin_bp.get("/calls/recent")
def calls_recent():
    ok, payload, code = _guard()
    if not ok: return payload, code
    limit = int(request.args.get("limit", 200))
    return jsonify({"ok": True, "events": recent(limit=limit)})

@admin_bp.post("/calls/clear")
def calls_clear():
    ok, payload, code = _guard()
    if not ok: return payload, code
    n = clear()
    return jsonify({"ok": True, "cleared": n})

@admin_bp.get("/calls/stream")
def calls_stream():
    ok, payload, code = _guard()
    if not ok: return payload, code
    def _events():
        last_len = 0
        while True:
            evs = recent(limit=400)
            if len(evs) > last_len:
                # send the delta
                for ev in evs[last_len:]:
                    yield "event: event\n" + "data: " + json.dumps(ev) + "\n\n"
                last_len = len(evs)
            time.sleep(0.7)
    return Response(stream_with_context(_events()), mimetype="text/event-stream")
