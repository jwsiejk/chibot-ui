from flask import Blueprint, request, session, abort, render_template, Response
from ..utils.admin import is_admin_email
from ..security_state import get_user
from collections import deque
import json, time

bp = Blueprint("admin_v1", __name__, url_prefix="/api/v1/admin")

# minimal in-memory queue and emitter (safe import target)
_LOG_Q = deque(maxlen=1000)
def _emit(kind: str, **fields):
    evt = {"ts": time.time(), "kind": kind}
    evt.update(fields or {})
    _LOG_Q.append(evt)
    return True

def _require_admin():
    email = get_user() or (session.get("user", {}) or {}).get("email") or session.get("email") or request.headers.get("X-User-Email")
    if not is_admin_email(email):
        abort(403)

@bp.get("/logs-ui")
def logs_ui():
    _require_admin()
    return render_template("admin_logs.html")

@bp.get("/logs")
def logs_sse():
    _require_admin()
    def stream():
        yield "event: heartbeat\n"
        yield "data: " + json.dumps({"ts": time.time(), "kind": "heartbeat", "msg": "ok"}) + "\n\n"
        # drain any queued events (short stream)
        while _LOG_Q:
            evt = _LOG_Q.popleft()
            yield "data: " + json.dumps(evt) + "\n\n"
    return Response(stream(), mimetype="text/event-stream")
