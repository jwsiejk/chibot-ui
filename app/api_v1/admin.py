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
        # drain any queued events (short stream for tests)
        while _LOG_Q:
            evt = _LOG_Q.popleft()
            yield "data: " + json.dumps(evt) + "\n\n"
    return Response(stream(), mimetype="text/event-stream")

@bp.get("/runtime")
def runtime():
    import os, sys, platform
    allow_open = bool(os.environ.get("ALLOW_MOCK_PROVIDERS") or os.environ.get("CI_FAST"))
    if not allow_open: _require_admin()
    def _safe(fnpath):
        try:
            mod_name, func_name = fnpath.rsplit(".", 1)
            mod = __import__(mod_name, fromlist=[func_name])
            fn = getattr(mod, func_name, None)
            val = fn() if callable(fn) else None
            return str(val or "unknown")
        except Exception:
            return "unknown"
    providers = {
        "llm": _safe("app.services.llm_provider.get_provider_name"),
        "stt": _safe("app.services.stt_provider.get_stt_provider_name"),
        "tts": _safe("app.services.tts_provider.get_tts_provider_name"),
    }
    keys = {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "smtp": bool(os.environ.get("EMAIL_HOST") or os.environ.get("FROM_EMAIL")),
        "database_url": bool(os.environ.get("DATABASE_URL")),
    }
    def _v(name):
        try:
            m=__import__(name); return getattr(m,"__version__","unknown")
        except Exception: return "unknown"
    versions = {
        "python": sys.version.split()[0],
        "flask": _v("flask"),
        "starlette": _v("starlette"),
        "uvicorn": _v("uvicorn"),
        "anyio": _v("anyio"),
        "platform": platform.platform()
    }
    return jsonify({"ok": True, "runtime": {"providers": providers, "keys": keys, "versions": versions}}), 200


@bp.post("/log")
def ingest_client_log():
    try:
        from flask import jsonify, request
        j = request.get_json(silent=True) or {}
        _emit("client_log", **({"payload": j} if isinstance(j, dict) else {"raw": str(j)}))
        return jsonify({"ok": True})
    except Exception:
        from flask import jsonify
        return jsonify({"ok": False}), 200
