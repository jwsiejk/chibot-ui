# app/api_v1/voice.py — /api/v1/voice/chunk production handler
from __future__ import annotations

import base64
import binascii
from flask import Blueprint, jsonify, request

from ..middleware.rate_limit import check_now
from ..api_v1.admin import _emit
from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice", __name__)

_MAX_CHUNK = 512 * 1024  # 512 KB guard (post-decode)

# ---- rate limit guard for all /voice/* ----
@bp.before_request
def _voice_rl_guard():
    ok, err = check_now("voice")
    if not ok:
        return jsonify(ok=False, error=err), 429

def _get_session_id() -> str | None:
    j = request.get_json(silent=True) or {}
    return (
        request.args.get("session_id")
        or j.get("session_id")
        or j.get("sid")
        or request.form.get("session_id")
        or request.headers.get("X-Session-Id")
    )

def _read_audio_bytes() -> bytes | None:
    # 1) multipart/form-data
    f = request.files.get("chunk")
    if f:
        try:
            return f.read()
        except Exception:
            return None

    # 2) JSON with base64
    if (request.content_type or "").startswith("application/json"):
        data = request.get_json(silent=True) or {}
        b64 = (
            data.get("audio_b64")
            or data.get("audio_base64")
            or data.get("b64")
            or data.get("data")
        )
        if isinstance(b64, str) and b64:
            try:
                return base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                return None

    # 3) raw bytes
    try:
        body = request.get_data(cache=False) or b""
        return body if body else None
    except Exception:
        return None

@bp.post("/chunk")
def voice_chunk():
    # NOTE: Kept this endpoint for production clients that post here directly.
    sess = _get_session_id()
    if not sess:
        return jsonify(ok=False, error="missing session_id"), 400

    data = _read_audio_bytes()
    if not data:
        return jsonify(ok=False, error="missing_or_invalid_chunk"), 400

    if len(data) > _MAX_CHUNK:
        return jsonify(ok=False, error="chunk too large", max_bytes=_MAX_CHUNK), 413

    try:
        get_manager().enqueue(sess, data)
    except Exception as e:
        return jsonify(ok=False, error="enqueue_failed", detail=str(e)), 500

    try:
        _emit("voice:chunk", "voice:chunk", session_id=sess, bytes=len(data))
    except Exception:
        pass

    return jsonify(ok=True), 200


# ---------- Legacy endpoints inside v1 → hard 410 (gone) ----------
@bp.post("/stt")
def legacy_stt():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410

@bp.post("/stt/stream")
def legacy_stt_stream():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410

@bp.post("/tts-with-visemes")
def legacy_tts():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410
