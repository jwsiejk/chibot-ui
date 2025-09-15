# app/api_v1/voice.py — /api/v1/voice/chunk production handler
from __future__ import annotations

import base64
import binascii
from flask import Blueprint, jsonify, request

from ..middleware.rate_limit import check_now
from ..api_v1.admin import _emit
from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice", __name__)

# ---- rate limit guard for all /voice/* ----
@bp.before_request
def _voice_rl_guard():
    rv = check_now("voice_chunk")
    if rv is not None:
        return rv

# Max decoded payload per chunk (bytes) — matches Diagnostics 413 guard
_MAX_BYTES = 262144  # 256 KiB

@bp.post("/chunk")
def post_voice_chunk():
    """
    Accepts JSON:
      {
        "session_id": "diag-xxxx",
        "user_msg_id": "diag-mic-yyy",
        "seq": 1,
        "b64": "<base64 of WebM/Opus bytes>"
      }
    Decodes to raw bytes and enqueues for the streaming ASR manager.
    Emits Admin SSE "voice:chunk" with decoded byte count.
    """
    js = request.get_json(force=True, silent=True) or {}

    sid = (js.get("session_id") or "").strip() or "diag-unknown"
    user_msg_id = (js.get("user_msg_id") or "").strip() or None
    seq = int(js.get("seq") or 0)

    b64 = js.get("b64") or ""
    if not b64:
        return jsonify(ok=False, error="missing_b64"), 400

    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return jsonify(ok=False, error="invalid_base64"), 400

    if not data:
        return jsonify(ok=False, error="empty_audio"), 400

    if len(data) > _MAX_BYTES:
        return jsonify(ok=False, error="chunk_too_large", max_bytes=_MAX_BYTES), 413

    # Enqueue REAL bytes to the streaming ASR manager
    get_manager().enqueue(sid, {"data": data, "user_msg_id": user_msg_id, "chunk_seq": seq})

    # Admin SSE: show decoded size so we can confirm non-zero bytes left the server
    try:
        _emit("voice:chunk", session_id=sid, seq=seq, bytes=len(data))
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
