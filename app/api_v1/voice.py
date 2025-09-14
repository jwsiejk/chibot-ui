
# app/api_v1/voice.py — /api/v1/voice/chunk production handler
from __future__ import annotations
import base64, binascii
from flask import Blueprint, jsonify, request
from ..middleware.rate_limit import check_now
from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice", __name__)

@bp.before_request
def _voice_rl_guard():
    rv = check_now('voice_chunk')
    if rv is not None:
        return rv

@bp.post("/chunk")
def chunk():
    j = request.get_json(silent=True) or {}
    sid = j.get("sid")
    b64 = j.get("audio_b64")
    chunk_seq = j.get("chunk_seq")
    user_msg_id = j.get("user_msg_id")
    if not sid or b64 is None or chunk_seq is None or user_msg_id is None:
        return jsonify(ok=False, error="bad_request", detail="sid, audio_b64, chunk_seq, user_msg_id required"), 400
    try:
        audio = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        return jsonify(ok=False, error="bad_audio_b64"), 400
    try:
        mgr = get_manager()
        mgr.enqueue(sid, {"data": audio, "user_msg_id": str(user_msg_id), "chunk_seq": int(chunk_seq)})
    except Exception as e:
        return jsonify(ok=False, error="enqueue_failed"), 500
    return jsonify(ok=True, received_seq=int(chunk_seq))

# ---------- Legacy endpoints: hard 410 (gone) ----------
@bp.post("/stt")
def legacy_stt():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410

@bp.post("/tts-with-visemes")
def legacy_tts():
    return jsonify(ok=False, error="gone", replacement="/api/v1/voice/chunk"), 410
