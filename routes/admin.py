# routes/admin.py
from flask import Blueprint, Response, jsonify, render_template, session, request, redirect, url_for
import os, json, time
from utils.call_log import call_log

admin_bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="../templates")

def _is_admin() -> bool:
    # Expect ADMIN_EMAILS="a@b.com,c@d.com". If not set, allow access (bootstrapping).
    admins = [e.strip().lower() for e in (os.getenv("ADMIN_EMAILS") or "").split(",") if e.strip()]
    if not admins:
        return True
    email = (session.get("user", {}) or {}).get("email") or session.get("email") or ""
    return (email or "").strip().lower() in admins

@admin_bp.before_request
def require_admin():
    if request.endpoint and request.endpoint.endswith(".stream"):
        # Let the stream itself handle auth for faster responses
        return None
    if not _is_admin():
        return jsonify({"ok": False, "error": "not_admin"}), 403

@admin_bp.get("/")
def admin_root():
    return redirect(url_for("admin.calls"))

@admin_bp.get("/calls")
def calls():
    return render_template("admin_call_log.html")

@admin_bp.get("/calls/recent")
def calls_recent():
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    return jsonify({"ok": True, "items": call_log.recent(limit)})

@admin_bp.post("/calls/clear")
def calls_clear():
    call_log.clear()
    return jsonify({"ok": True})

@admin_bp.get("/stream")
def stream():
    # SSE live stream of new log events
    def gen():
        q = call_log.subscribe()
        try:
            while True:
                item = q.get()
                yield "data: " + json.dumps(item) + "\n\n"
        except GeneratorExit:
            pass
        finally:
            call_log.unsubscribe(q)
    hdrs = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(gen(), mimetype="text/event-stream", headers=hdrs)
