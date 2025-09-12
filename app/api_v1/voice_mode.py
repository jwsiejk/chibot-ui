# app/api_v1/voice_mode.py
from flask import Blueprint, jsonify

bp = Blueprint("voice_mode_v1", __name__, url_prefix="/api/v1/voice")

@bp.get("/stt-mode")
def stt_mode():
    # Streaming is the only lane now.
    return jsonify(stt_mode="stream"), 200
