# routes/admin.py
from flask import Blueprint, Response, render_template, session
import os, json
from utils.call_log import call_log

# We render a template, so point to ../templates from routes/
admin_bp = Blueprint("admin_bp", __name__, template_folder="../templates")

def _is_admin() -> bool:
    # Expect ADMIN_EMAILS="a@b.com,c@d.com"
    admins = [e.strip().lower() for e in (os.getenv("ADMIN_EMAILS") or "").split(",") if e.strip()]
    if not admins:
        # If not configured, allow access (helps during setup)
        return True
    user_email = (session.get("user", {}) or {}).get("email") or session.get("email") or ""
    return (user_email or "").lower() in admins

@admin_bp.route("/")
def admin_index():
    if not _is_admin():
        # Minimal 403 to avoid leaking anything
        return ("Forbidden (not in ADMIN_EMAILS)", 403)
    return render_template("admin_call_log.html")

@admin_bp.route("/stream")
def stream():
    def gen():
        q = call_log.subscribe()
        try:
            while True:
                item = q.get()
                yield f"data: {json.dumps(item)}\n\n"
        except GeneratorExit:
            pass
        finally:
            call_log.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream")
