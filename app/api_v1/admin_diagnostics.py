# app/api_v1/admin_diagnostics.py
# Diagnostics endpoints used by the Admin console.
# NOTE: lives directly under api_v1 (no subfolder).

from __future__ import annotations

import os
from flask import Blueprint, jsonify, request

from ..services.streaming_asr.stream_manager import get_manager

# URL pattern: /api/v1/admin/diagnostics/...
bp = Blueprint("admin_diagnostics", __name__, url_prefix="/api/v1/admin/diagnostics")


def _has(v: str | None) -> bool:
    return bool(v and v.strip())


@bp.route("/vendor_status", methods=["GET", "POST"])
def vendor_status():
    """
    Returns whether required vendor keys are present on the server.
    Admin UI reads this for 'vendor_keys_ok'.
    """
    deepgram_ok = _has(os.getenv("DEEPGRAM_API_KEY"))
    eleven_key_ok = _has(os.getenv("ELEVENLABS_API_KEY"))
    eleven_voice_ok = _has(os.getenv("ELEVENLABS_VOICE_ID"))
    return jsonify(
        ok=True,
        deepgram=deepgram_ok,
        elevenlabs=eleven_key_ok and eleven_voice_ok,
        elevenlabs_key=eleven_key_ok,
        elevenlabs_voice=eleven_voice_ok,
    ), 200


@bp.route("/streaming_status", methods=["GET", "POST"])
def streaming_status():
    """
    Returns ASR streaming counters.
    - If ?sid=... provided, returns stats for that session id.
    - Otherwise returns an aggregate across active sessions.
    Admin UI uses partials/finals/asr_error to drive the checks.
    """
    sid = request.args.get("sid") or request.args.get("session_id")
    mgr = get_manager()

    if sid:
        s = mgr.stats(sid)
        return jsonify(
            ok=True,
            sid=sid,
            partials=int(s.get("partials", 0)),
            finals=int(s.get("finals", 0)),
            asr_error=bool(s.get("err")),
            err=s.get("err"),
        ), 200

    agg = mgr.stats_all()
    return jsonify(
        ok=True,
        partials=int(agg.get("partials", 0)),
        finals=int(agg.get("finals", 0)),
        asr_error=agg.get("err_count", 0) > 0,
        err_count=int(agg.get("err_count", 0)),
        sessions=agg.get("sessions", {}),
    ), 200


@bp.route("/rate_limits", methods=["GET", "POST"])
def rate_limits():
    """
    Lightweight health for rate limit check in Diagnostics.
    If you later expose real counters, add them here.
    """
    return jsonify(ok=True, status2=200), 200
