from __future__ import annotations

import os
import platform
import secrets
import sys
import time
from flask import Blueprint, request, session, abort, jsonify
from werkzeug.exceptions import HTTPException

from ..utils.admin import is_admin_email
from ..security_state import get_user
from ..services.config_store import get_config
from ..services import admin_settings as cfg
from ..services import test_runner as testr
from ..admin_log import (
    admin_log_emit as _admin_log_emit_core,
    get_admin_log_history,
)

# add the prefix here so route is /api/v1/admin/logs
bp = Blueprint("admin", __name__, url_prefix="/api/v1/admin")
# ----------------- Admin access helpers -----------------

def _require_admin() -> None:
    email = (session.get("user") or {}).get("email") or request.headers.get("X-User-Email") or (get_user() or "")
    if not is_admin_email((email or "").strip().lower()):
        abort(403)

# ----------------- Admin event log helpers -----------------


def admin_log_emit(evt: dict | None) -> dict | None:
    if not isinstance(evt, dict):
        return None
    recorded = _admin_log_emit_core(evt)
    if recorded:
        try:
            _mirror_ws_event(recorded.get("event", "log"), event=recorded)
        except Exception:
            pass
    return recorded


def _extract_admin_token(payload: dict | None = None) -> str | None:
    """Return a token supplied via query/header/payload for admin log access."""

    candidates: list[str] = []

    query_token = request.args.get("k")
    if isinstance(query_token, str) and query_token.strip():
        candidates.append(query_token.strip())

    header_token = request.headers.get("X-Admin-SSE-Token") or request.headers.get("X-Admin-Token")
    if isinstance(header_token, str) and header_token.strip():
        candidates.append(header_token.strip())

    auth_header = request.headers.get("Authorization", "")
    if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
        bearer = auth_header[7:].strip()
        if bearer:
            candidates.append(bearer)

    if isinstance(payload, dict):
        token_value = payload.get("token")
        if isinstance(token_value, str) and token_value.strip():
            candidates.append(token_value.strip())

    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _has_valid_admin_token(payload: dict | None = None) -> bool:
    expected = (os.environ.get("ADMIN_SSE_E2E_KEY") or "").strip()
    if not expected:
        return False

    provided = _extract_admin_token(payload)
    if not provided:
        return False

    try:
        return secrets.compare_digest(provided, expected)
    except Exception:
        return provided == expected

def _mirror_ws_event(kind: str, *, event: dict) -> None:
    """Mirror admin diagnostics to the WS session stream when possible."""
    sid = event.get("session_id") or event.get("sid")
    if not sid:
        return

    try:
        sid_str = str(sid)
    except Exception:
        sid_str = sid  # type: ignore[assignment]

    frame = {"type": kind}
    frame.update(event)
    frame.setdefault("session_id", sid_str)
    frame.setdefault("sid", sid_str)

    try:
        from app.ws.bus import bus as _bus  # local import to avoid circular deps
    except Exception:
        _bus = None

    if _bus is None:
        return

    try:
        _bus.broadcast(str(sid_str), frame)
    except Exception:
        pass


def _emit(kind: str, *, label: str | None = None, route: str | None = None, **fields) -> bool:
    """Append an admin log event (and bump step)."""
    try:
        base = label or kind
        if route:
            base = f"{base} {route}"
        evt = {
            "event": kind,
            "kind": kind,
            "label": base,
            "route": route,
            **(fields or {}),
        }
        return bool(admin_log_emit(evt))
    except Exception:
        return False

@bp.get("/logs")
def logs_snapshot():
    """Return the current admin log history as JSON."""

    try:
        _require_admin()
    except HTTPException as exc:
        if getattr(exc, "code", None) != 403 or not _has_valid_admin_token():
            raise

    limit_value = request.args.get("limit")
    after_value = request.args.get("after") or request.args.get("after_step")

    try:
        limit = int(limit_value) if limit_value is not None else None
    except (TypeError, ValueError):
        limit = None

    try:
        after_step = int(after_value) if after_value is not None else None
    except (TypeError, ValueError):
        after_step = None

    all_events = get_admin_log_history()
    total = len(all_events)
    latest_step = int(all_events[-1]["step"]) if all_events and "step" in all_events[-1] else 0

    events = all_events
    if after_step is not None:
        events = [evt for evt in events if int(evt.get("step", 0)) > after_step]

    if limit is not None and limit >= 0:
        events = events[-limit:]

    return (
        jsonify({
            "ok": True,
            "events": events,
            "total": total,
            "latest_step": latest_step,
        }),
        200,
    )

@bp.post("/log")
def logs_append():
    """Append a custom admin log event from the UI diagnostic tools."""
    payload_obj = request.get_json(silent=True)
    payload_dict = payload_obj if isinstance(payload_obj, dict) else {}

    if not _has_valid_admin_token(payload_dict):
        _require_admin()

    filtered_payload = {k: v for k, v in payload_dict.items() if k != "token"}

    kind = (filtered_payload.get("kind") or "admin_diag").strip() if isinstance(filtered_payload.get("kind"), str) else "admin_diag"
    label = filtered_payload.get("label") if isinstance(filtered_payload.get("label"), str) else None
    route = filtered_payload.get("route") if isinstance(filtered_payload.get("route"), str) else None

    extra = {k: v for k, v in filtered_payload.items() if k not in {"kind", "label", "route"}}
    if "payload" not in extra:
        extra["payload"] = filtered_payload

    event_payload = {
        "event": kind,
        "kind": kind,
        "label": label or kind,
        "route": route,
        **extra,
    }

    ok = bool(admin_log_emit(event_payload))

    return jsonify({"ok": bool(ok), "kind": kind, "label": label}), 200 if ok else 202

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

# ----------------- Shared vendor truth -----------------

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

def _no_store(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ----------------- Admin config API -----------------

@bp.get("/config")
def get_settings_api():
    _require_admin()
    payload = {"ok": True, "settings": cfg.get_settings(), "vendors": _vendor_status_payload()}
    return _no_store(jsonify(payload)), 200

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

@bp.route("/diagnostics/vendor_status", methods=["GET", "POST"])
def _diag_vendor_status():
    _require_admin()
    return _no_store(jsonify(_vendor_status_payload())), 200

@bp.route("/diagnostics/streaming_status", methods=["GET", "POST"])
def _diag_streaming_status():
    _require_admin()
    # Import inside to avoid circular import at module import time
    from ..services.streaming_asr.stream_manager import get_manager  # type: ignore

    sid = request.args.get("sid") or request.args.get("session_id")
    mgr = get_manager()

    if sid:
        s = mgr.stats(sid)
        return _no_store(jsonify(
            ok=True,
            sid=sid,
            partials=int(s.get("partials", 0)),
            finals=int(s.get("finals", 0)),
            provider_errors=int(s.get("provider_errors", 0)),
            asr_error=bool(s.get("err")),
            err=s.get("err"),
        )), 200

    if hasattr(mgr, "stats_all"):
        agg = mgr.stats_all()  # type: ignore[attr-defined]
        return _no_store(jsonify(
            ok=True,
            partials=int(agg.get("partials", 0)),
            finals=int(agg.get("finals", 0)),
            asr_error=agg.get("err_count", 0) > 0,
            err_count=int(agg.get("err_count", 0)),
            provider_errors=int(agg.get("provider_errors", 0)),
            sessions=agg.get("sessions", {}),
        )), 200

    return _no_store(jsonify(ok=True, partials=0, finals=0, asr_error=False)), 200

@bp.route("/diagnostics/rate_limits", methods=["GET", "POST"])
def _diag_rate_limits():
    _require_admin()
    return jsonify(ok=True, status2=200), 200


# ----------------- KB Seed (Phase 8) -----------------

@bp.post("/kb/seed")
def kb_seed():
    _require_admin()
    from ..services.retrieval import add_document
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip() or "Untitled"
    body  = (data.get("body") or "").strip()
    tags  = (data.get("tags") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "body required"}), 400
    doc_id = add_document(title, body, tags)
    try:
        _emit("kb:seed", doc_id=doc_id, title=title)
    except Exception:
        pass
    return jsonify({"ok": True, "doc_id": doc_id}), 200


@bp.post("/layouts")
def post_layouts():
    _require_admin()
    data = request.get_json(silent=True) or {}
    bp_name = (data.get("breakpoint") or "desktop").strip()
    js = data.get("json") or {}
    from ..db import db
    mem = db.memory.setdefault("layouts", {})
    history = mem.setdefault(bp_name, {"history": [], "current": None})
    ver = (history["current"] or 0) + 1
    history["history"].append({"version": ver, "json": js})
    history["current"] = ver
    try:
        _emit("layout_updated", breakpoint=bp_name, version=ver)
    except Exception:
        pass
    return jsonify({"ok": True, "breakpoint": bp_name, "version": ver}), 200

@bp.post("/layouts/rollback")
def post_layouts_rollback():
    _require_admin()
    data = request.get_json(silent=True) or {}
    bp_name = (data.get("breakpoint") or "desktop").strip()
    target_ver = int(data.get("version") or 0)
    from ..db import db
    mem = db.memory.setdefault("layouts", {})
    history = mem.setdefault(bp_name, {"history": [], "current": None})
    ok = False
    for item in history["history"]:
        if item.get("version") == target_ver:
            history["current"] = target_ver
            ok = True
            break
    try:
        _emit("layout_rollback", breakpoint=bp_name, version=target_ver, ok=ok)
    except Exception:
        pass
    return jsonify({"ok": ok, "breakpoint": bp_name, "version": history.get("current")}), 200



@bp.get("/sessions")
def list_sessions():
    _require_admin()
    from ..db import db
    sess = db.memory.get("sessions", {})
    out = [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in sess.items()]
    return jsonify({"ok": True, "sessions": out}), 200


@bp.get("/ws-metrics")
def ws_metrics_page():
    _require_admin()
    from flask import render_template
    return render_template("admin_ws_metrics.html")
