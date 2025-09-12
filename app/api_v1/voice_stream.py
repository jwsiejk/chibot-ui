# app/api_v1/voice_stream.py
from __future__ import annotations
from flask import Blueprint, request, jsonify

from ..services.streaming_asr.stream_manager import get_manager

bp = Blueprint("voice_stream_v1", __name__, url_prefix="/api/v1/voice")

@bp.post("/stt/stream")
def voice_stt_stream():
    sess = request.args.get("session_id") or request.form.get("session_id") or "default"

    # Accept either multipart/form-data (chunk) or raw octet-stream
    data = b""
    f = request.files.get("chunk")
    if f:
        data = f.read()
    else:
        data = request.get_data(cache=False) or b""

    if not data:
        return jsonify(error="missing chunk"), 400
    if len(data) > 512 * 1024:
        return jsonify(error="chunk too large"), 413

    mgr = get_manager()
    mgr.enqueue(sess, data)
    return jsonify(ok=True), 200
