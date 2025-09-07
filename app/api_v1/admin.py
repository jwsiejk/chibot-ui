from flask import Blueprint, request, session, abort, render_template, Response, jsonify
from ..utils.admin import is_admin_email
from ..security_state import get_user
from collections import deque
import json, time, os, sys, platform

bp = Blueprint("admin_v1", __name__, url_prefix="/api/v1/admin")

# --- minimal in-memory event queue for admin log ---
_LOG_Q = deque(maxlen=1000)

def _emit(kind: str, **fields):
    try:
        evt = {"ts": time.time(), "kind": kind}
        if fields:
            evt.update(fields)
        _LOG_Q.append(evt)
        return True
    except Exception:
        return False

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
        fast = bool(os.environ.get("CI_FAST"))
        # continuous stream in prod; single loop in CI_FAST mode
        while True:
            # flush queued events first
            while _LOG_Q:
                evt = _LOG_Q.popleft()
                yield "data: " + json.dumps(evt) + "\n\n"
            # heartbeat
            yield "event: heartbeat\n"
            yield "data: " + json.dumps({"ts": time.time(), "kind": "heartbeat", "msg": "ok"}) + "\n\n"
            if fast:
                break
            try:
                time.sleep(1.2)
            except Exception:
                break
    return Response(stream(), mimetype="text/event-stream")

@bp.get("/runtime")
def runtime():
    """Lightweight runtime info for diagnostics/tests.
    Security: open when ALLOW_MOCK_PROVIDERS or CI_FAST is set; otherwise require admin.
    Shape: {"ok":true,"runtime":{"providers":{...},"keys":{...},"versions":{...}}}
    """
    allow_open = bool(os.environ.get("ALLOW_MOCK_PROVIDERS") or os.environ.get("CI_FAST"))
    if not allow_open:
        _require_admin()

    # providers (best-effort; never fail)
    def _safe(fnpath: str):
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

    # keys (booleans only; never leak secrets)
    keys = {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "smtp": bool(os.environ.get("EMAIL_HOST") or os.environ.get("FROM_EMAIL")),
        "database_url": bool(os.environ.get("DATABASE_URL")),
    }

    def _v(modname: str):
        try:
            m = __import__(modname)
            return getattr(m, "__version__", "unknown")
        except Exception:
            return "unknown"

    versions = {
        "python": sys.version.split()[0],
        "flask": _v("flask"),
        "starlette": _v("starlette"),
        "uvicorn": _v("uvicorn"),
        "anyio": _v("anyio"),
        "platform": platform.platform()
    }

    out = {"providers": providers, "keys": keys, "versions": versions}
    return jsonify({"ok": True, "runtime": out}), 200
