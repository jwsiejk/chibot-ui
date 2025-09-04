from __future__ import annotations
from flask import Blueprint, request, jsonify

bp = Blueprint("voice_v1", __name__)

@bp.post("/voice/stt")
def stt():
    # Phase 3 will implement STT handling.
    if "file" not in request.files:
        return jsonify(ok=False, error="no_file"), 400
    return jsonify(ok=False, error="not_implemented"), 501

@bp.post("/voice/tts-with-visemes")
def tts_with_visemes():
    data = request.get_json(silent=True) or {}
    if not data.get("text"):
        return jsonify(ok=False, error="empty_text"), 400
    return jsonify(ok=False, error="not_implemented"), 501
