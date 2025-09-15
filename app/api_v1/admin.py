from flask import Blueprint, request, session, abort, render_template, Response
from ..utils.admin import is_admin_email
from ..security_state import get_user
import json, time
from ..services.config_store import get_config


# --- minimal in-memory event queue for admin log ---
from collections import deque
_LOG_Q = deque(maxlen=1000)


_STEP = 0

def _emit(kind: str, **fields):
    global _STEP
    try:
        import time, json
        _STEP += 1
        route = fields.pop("route", None)
        label = fields.pop("label", None)
        if not label:
            base = kind
            if route:
                base += f" – {route}"
            label = base
        evt = {
            "ts": time.time(),
            "step": _STEP,
            "kind": kind,
            "route": route,
            "label": label,
            **(fields or {})
        }
        _LOG_Q.append(evt)
        return True
    except Exception:
        return False

bp = Blueprint("admin_v1", __name__, url_prefix="/api/v1/admin")

def _require_admin():
    email = (session.get("user") or {}).get("email") or session.get("email") or request.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)

@bp.get("/logs-ui")
def logs_ui():
    _require_admin()
    return render_template("admin_logs.html")


@bp.get("/logs")
def logs_sse():
    _require_admin()
    live = request.args.get("live") in ("1","true","yes")
    def stream():
        import time as _t
        # initial heartbeat
        yield "event: heartbeat
"
        yield "data: " + json.dumps({"ts": _t.time(), "kind": "heartbeat", "msg": "ok"}) + "

"
        yield "data: " + json.dumps({"ts": _t.time(), "kind": "ping"}) + "

"
        last_hb = _t.time()
        while True:
            sent = False
            while _LOG_Q:
                evt = _LOG_Q.popleft()
                yield "data: " + json.dumps(evt) + "

"
                sent = True
            # keep-alive heartbeats
            now = _t.time()
            if now - last_hb > 5:
                yield "event: heartbeat
"
                yield "data: " + json.dumps({"ts": now, "kind": "heartbeat", "msg": "ok"}) + "

"
                yield "data: " + json.dumps({"ts": now, "kind": "ping"}) + "

"
                last_hb = now
            if not live and not sent:
                break
            _t.sleep(0.3)
    return Response(stream(), mimetype="text/event-stream")



@bp.get("/runtime")
def runtime():
    import os, sys, platform
    allow_open = bool(os.environ.get("ALLOW_MOCK_PROVIDERS") or os.environ.get("CI_FAST"))
    if not allow_open: _require_admin()
    cfg = get_config()
    def _safe(fnpath):
        try:
            mod_name, func_name = fnpath.rsplit(".", 1)
            mod = __import__(mod_name, fromlist=[func_name])
            fn = getattr(mod, func_name, None)
            return str(fn(cfg) if callable(fn) else "unknown")
        except Exception:
            return "unknown"
    providers = {
        "llm": _safe("app.services.llm_provider.get_provider_name"),
        "stt": _safe("app.services.stt_provider.get_stt_provider_name"),
        "tts": _safe("app.services.tts_provider.get_tts_provider_name"),
    }
    def _v(name):
        try:
            mod = __import__(name, fromlist=["__version__"])
            return getattr(mod, "__version__", "unknown")
        except Exception:
            return "unknown"
    versions = {
        "anyio": _v("anyio"),
        "flask": _v("flask"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "starlette": _v("starlette"),
        "uvicorn": _v("uvicorn"),
        "websockets": _v("websockets"),
    }
    return jsonify({"ok": True, "runtime": {"keys": {
        "database_url": bool(os.environ.get("DATABASE_URL")),
        "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "smtp": bool(os.environ.get("EMAIL_HOST")),
    }, "providers": providers, "versions": versions}}), 200



from flask import jsonify
from ..db import db

@bp.get("/config")
def config_get():
    _require_admin()
    cfg = db.get_config()
    return jsonify({"ok": True, "config": cfg}), 200

@bp.post("/config")
def config_post():
    _require_admin()
    data = request.get_json(silent=True) or {}
    updates = data.get("updates") or {}
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "invalid_updates"}), 400
    db.update_config(updates)
    try:
        _emit('config_updated', updates=updates)
    except Exception:
        pass
    return jsonify({"ok": True}), 200

@bp.post("/diag/run")
def diag_run():
    # Called by /diagnostics to kick an event and prove admin API is alive
    try:
        _emit('diag', msg='requested')
    except Exception:
        pass
    return jsonify({"ok": True}), 200


from flask import jsonify, request
from ..services import admin_settings as cfg

@bp.get("/config")
def get_settings_api():
    return jsonify({"ok": True, "settings": cfg.get_settings(), "vendors": cfg.vendor_status()})

@bp.post("/config")
def post_settings_api():
    payload = request.get_json(silent=True) or {}
    updated = cfg.update_settings(payload)
    return jsonify({"ok": True, "settings": updated})


# --- Test run control & logs (pinned) ---
from flask import Response
from ..services import test_runner as testr

@bp.post("/test-runs")
def start_test_run():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "voice")).lower()
    if mode not in ("voice","chat"):
        mode = "voice"
    run_id = testr.start_test(mode)
    return jsonify({"ok": True, "id": run_id})

@bp.get("/test-runs")
def list_test_runs():
    return jsonify({"ok": True, "items": testr.list_runs()})

@bp.get("/test-runs/<run_id>")
def get_test_run(run_id: str):
    data = testr.get(run_id)
    if not data:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "item": data})

@bp.get("/test-runs/<run_id>/json")
def get_test_run_json(run_id: str):
    data = testr.get(run_id)
    if not data:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify(data)

@bp.get("/test-runs/<run_id>/sse")
def sse_test_run(run_id: str):
    def gen():
        last = 0
        while True:
            data = testr.get(run_id)
            if not data:
                break
            logs = data.get("logs", [])
            if last < len(logs):
                chunk = logs[last:]
                last = len(logs)
                yield f"data: {json.dumps(chunk)}\n\n"
            if data.get("status") in ("ok","fail"):
                break
            import time as _t; _t.sleep(0.35)
    return Response(gen(), mimetype="text/event-stream")
