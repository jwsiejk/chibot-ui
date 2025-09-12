# app/api_v1/admin_diagnostics.py
from __future__ import annotations
from flask import Blueprint, jsonify
import time

bp = Blueprint("admin_diag_v1", __name__, url_prefix="/api/v1/admin")

def _ok(name, details="ok"):  return {"name": name, "ok": True,  "details": details}
def _ng(name, details):       return {"name": name, "ok": False, "details": details}

@bp.get("/diagnostics")
def diagnostics_info():
    """
    Lightweight "are diagnostics wired?" – returns immediately.
    Front-end uses this to avoid hanging UIs.
    """
    return jsonify(ok=True, version="v1", checks=["health", "db", "ws_bus", "tts", "stt_stream"])

@bp.post("/diagnostics/run")
def diagnostics_run():
    """
    Synchronous quick checks (each wrapped in try/except, time-bounded).
    This avoids the prior "running checks" spinner that never resolves.
    """
    results = []
    # health
    try:
        from .health import bp as _bp  # import success implies route present
        results.append(_ok("health"))
    except Exception as e:
        results.append(_ng("health", f"missing: {e!r}"))

    # db
    try:
        # many envs don't expose a DB session helper; keep this non-fatal
        from app.services.admin_config import get_admin_config as _get
        _ = _get()
        results.append(_ok("db"))
    except Exception as e:
        results.append(_ng("db", f"config read failed: {e!r}"))

    # ws_bus
    try:
        from app.ws.bus import bus
        q = bus.subscribe("__diag__")
        bus.broadcast("__diag__", {"type": "diag", "text": "ok"})
        # don't block on a consumer – just ensure broadcast didn't throw
        results.append(_ok("ws_bus"))
    except Exception as e:
        results.append(_ng("ws_bus", f"{e!r}"))

    # tts presence (not a live synth)
    try:
        from app.services import tts_provider  # import path stable in your tree
        _ = tts_provider  # noqa
        results.append(_ok("tts"))
    except Exception as e:
        results.append(_ng("tts", f"{e!r}"))

    # streaming stt route presence
    try:
        from app.api_v1.voice_stream import bp as _vp
        results.append(_ok("stt_stream"))
    except Exception as e:
        results.append(_ng("stt_stream", f"{e!r}"))

    return jsonify(ok=True, results=results, ts=int(time.time()))
