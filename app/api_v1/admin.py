from __future__ import annotations

import json
import os
import sys
import time
import platform
from collections import deque
from flask import Blueprint, request, session, abort, render_template, Response, jsonify

from ..utils.admin import is_admin_email
from ..security_state import get_user
from ..services.config_store import get_config
from ..services import admin_settings as cfg
from ..services import test_runner as testr

bp = Blueprint("admin", __name__)

# ----------------- Admin access helpers -----------------

def _require_admin() -> None:
    email = (session.get("user") or {}).get("email") or request.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)

# ----------------- Admin event log (SSE) ----------------

_LOG_Q = deque(maxlen=1000)
_STEP = 0

def _emit(kind: str, *, label: str | None = None, route: str | None = None, **fields) -> bool:
    """Append an admin log event (and bump step)."""
    try:
        global _STEP
        _STEP += 1
        base = label or kind
        if route:
            base = f"{base} {route}"
        evt = {
            "ts": time.time(),
            "step": _STEP,
            "kind": kind,
            "route": route,
            "label": base,
            **(fields or {})
        }
        _LOG_Q.append(evt)
        return True
    except Exception:
        return False

@bp.get("/logs")
def logs_sse():
    """
    Live admin logs stream.
    - /api/v1/admin/logs           → short drain (ends when queue drains)
    - /api/v1/admin/logs?live=1    → live tail; heartbeats + 'ping' unlabeled messages
    """
    _require_admin()
    live = request.args.get("live") in ("1", "true", "yes")

    def stream():
        import time as _t

        # initial heartbeat (named) and unlabeled ping so onmessage fires
        yield "event: heartbeat\n"
        yield "data: " + json.dumps({"ts": _t.time(), "kind": "heartbeat", "msg": "ok"}) + "\n\n"
        yield "data: " + json.dumps({"ts": _t.time(), "kind": "ping"}) + "\n\n"

        last_hb = _t.time()
        while True:
            sent = False
            while _LOG_Q:
                evt = _LOG_Q.popleft()
                yield "data: " + json.dumps(evt) + "\n\n"
                sent = True

            now = _t.time()
            if now - last_hb > 5:
                yield "event: heartbeat\n"
                yield "data: " + json.dumps({"ts": now, "kind": "heartbeat", "msg": "ok"}) + "\n\n"
                yield "data: " + json.dumps({"ts": now, "kind": "ping"}) + "\n\n"
                last_hb = now

            if not live and not sent:
                break
            _t.sleep(0.3)

    return Response(stream(), mimetype="text/event-stream")

# ----------------- Runtime snapshot -----------------

@bp.get("/runtime")
def runtime():
    # Admin-only unless explicitly allowed (no vendor calls here; display only)
    allow_open = bool(os.environ.get("ALLOW_MOCK_PROVIDERS") or os.environ.get("CI_FAST"))
    if not allow_open:
        _require_admin()

    cfg_obj = get_config()

    def _safe(fnpath: str) -> str:
        try:
            mod_name, func_name = fnpath.rsplit(".", 1)
            mod = __import__(mod_name, fromlist=[func_name])
            fn = getattr(mod, func_name, None)
            return str(fn(cfg_obj) if callable(fn) else "unknown")
        except Exception:
            return "unknown"

    providers = {
        "llm": _safe("app.services.llm_provider.get_provider_name"),
        "stt": _safe("app.services.stt_provider.get_stt_provider_name"),
        "tts": _safe("app.services.tts_provider.get_tts_provider_name"),
    }

    def _v(name: str) -> str:
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

    keys = {
        "database_url": bool(os.environ.get("DATABASE_URL")),
        "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "smtp": bool(os.environ.get("EMAIL_HOST") or os.environ.get("EMAIL_HOST_USER")),
    }

    return jsonify({"ok": True, "runtime": {"keys": keys, "providers": providers, "versions": versions}}), 200

# ----------------- Admin config API -----------------

def _vendor_status_payload() -> dict:
    """Single source of truth for vendor key presence."""
    deepgram_ok = bool(os.environ.get("DEEPGRAM_API_KEY"))
    eleven_key_ok = bool(os.environ.get("ELEVENLABS_API_KEY"))
    eleven_voice_ok = bool(os.environ.get("ELEVENLABS_VOICE_ID"))
    return {
        "ok": True,
        "deepgram": deepgram_ok,
        "elevenlabs": (eleven_key_ok and eleven_voice_ok),
        "elevenlabs_key": eleven_key_ok,
        "elevenlabs_voice": eleven_voice_ok,
    }

@bp.get("/config")
def get_settings_api():
    _require_admin()
    # Keep settings from cfg, but compute vendors here so Diagnostics and /config agree
    return jsonify({"ok": True, "settings": cfg.get_settings(), "vendors": _vendor_status_payload()}), 200

@bp.post("/config")
def post_settings_api():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    updated = cfg.update_settings(payload)
    return jsonify({"ok": True, "settings": updated}), 200

# ----------------- Diagnostics runner & hooks -----------------

@bp.post("/diag/run")
def diag_run():
    try:
        _emit("diag", msg="requested")
    except Exception:
        pass
    return jsonify({"ok": True}), 200

@bp.get("/diag/stream")
def diag_stream():
    _require_admin()
    run_id = request.args.get("run_id", "")
    if not run_id:
        return Response("event: error\ndata: {\"error\":\"missing run_id\"}\n\n", mimetype="text/event-stream")

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
            if data.get("status") in ("ok", "fail"):
                break
            import time as _t
            _t.sleep(0.35)

    return Response(gen(), mimetype="text/event-stream")

# ----------------- Admin Diagnostics (GET+POST) -----------------
# These match what the Admin UI calls; both return the same vendor truth.

@bp.route("/diagnostics/vendor_status", methods=["GET", "POST"])
def _diag_vendor_status():
    _require_admin()
    payload = _vendor_status_payload()
    # explicit no-store to avoid any caching weirdness
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp, 200

@bp.route("/diagnostics/streaming_status", methods=["GET", "POST"])
def _diag_streaming_status():
    _require_admin()
    # Import inside to avoid circular import at module import time
    from ..services.streaming_asr.stream_manager import get_manager  # type: ignore

    sid = request.args.get("sid") or request.args.get("session_id")
    mgr = get_manager()

    if sid:
        s = mgr.stats(sid)
        resp = jsonify(
            ok=True,
            sid=sid,
            partials=int(s.get("partials", 0)),
            finals=int(s.get("finals", 0)),
            asr_error=bool(s.get("err")),
            err=s.get("err"),
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp, 200

    if hasattr(mgr, "stats_all"):
        agg = mgr.stats_all()  # type: ignore[attr-defined]
        resp = jsonify(
            ok=True,
            partials=int(agg.get("partials", 0)),
            finals=int(agg.get("finals", 0)),
            asr_error=agg.get("err_count", 0) > 0,
            err_count=int(agg.get("err_count", 0)),
            sessions=agg.get("sessions", {}),
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp, 200

    return jsonify(ok=True, partials=0, finals=0, asr_error=False), 200

@bp.route("/diagnostics/rate_limits", methods=["GET", "POST"])
def _diag_rate_limits():
    _require_admin()
    return jsonify(ok=True, status2=200), 200
