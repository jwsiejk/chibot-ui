from flask import Blueprint, request, session, abort, render_template, Response
from ..utils.admin import is_admin_email
from ..security_state import get_user
import json, time


# --- minimal in-memory event queue for admin log ---
from collections import deque
_LOG_Q = deque(maxlen=1000)

def _emit(kind:str, **fields):
    try:
        import time, json
        evt = {"ts": time.time(), "kind": kind}
        evt.update(fields or {})
        _LOG_Q.append(evt)
        return True
    except Exception:
        return False

bp = Blueprint("admin_v1", __name__, url_prefix="/api/v1/admin")

@bp.get("/runtime")
def runtime():
    """Lightweight runtime info for diagnostics/tests.
    Security: open when ALLOW_MOCK_PROVIDERS or CI_FAST is set; otherwise require admin.
    """
    import os, sys, platform, json
    allow_open = bool(os.environ.get("ALLOW_MOCK_PROVIDERS") or os.environ.get("CI_FAST"))
    if not allow_open:
        try:
            _require_admin()
        except Exception:
            # fallthrough to 403
            from flask import jsonify
            return jsonify({"ok": False, "error": "forbidden"}), 403
    # Provider names (best-effort; never crash)
    def _safe(name, fnpath):
        try:
            mod_name, func_name = fnpath.rsplit(".", 1)
            mod = __import__(mod_name, fromlist=[func_name])
            fn = getattr(mod, func_name, None)
            val = fn() if callable(fn) else None
            return str(val or "unknown")
        except Exception:
            return "unknown"
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "llm_provider": _safe("llm", "app.services.llm_provider.get_provider_name"),
        "stt_provider": _safe("stt", "app.services.stt_provider.get_stt_provider_name"),
        "tts_provider": _safe("tts", "app.services.tts_provider.get_tts_provider_name"),
    }
    try:
        from flask import jsonify
        return jsonify({"ok": True, "runtime": info}), 200
    except Exception:
        return {"ok": True, "runtime": info}, 200

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
    import os
    _require_admin()
    def stream():
        yield "event: heartbeat\n"
        yield "data: " + json.dumps({"ts": time.time(), "kind": "heartbeat", "msg": "ok"}) + "\n\n"
        # drain any queued events (short stream for tests)
        while _LOG_Q:
            evt = _LOG_Q.popleft()
            yield "data: " + json.dumps(evt) + "\n\n"
    return Response(stream(), mimetype="text/event-stream")
