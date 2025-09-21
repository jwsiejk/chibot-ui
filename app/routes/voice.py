
from flask import Blueprint, request, jsonify
from .tts_provider import get_tts_provider
import base64

bp_voice = Blueprint("bp_voice", __name__, url_prefix="/api/v1/voice")

@bp_voice.post("/tts-with-visemes")
def tts_with_visemes():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip() or "Hello"
    tts = get_tts_provider()
    audio_bytes, visemes = tts.synthesize_with_visemes(text)
    return jsonify({
        "ok": True,
        "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
        "visemes": visemes
    })
