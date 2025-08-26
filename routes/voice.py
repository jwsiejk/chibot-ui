# routes/voice.py
from flask import Blueprint, request, jsonify, current_app
from services.tts_bridge import synthesize_with_visemes
from utils.call_log import call_log

voice_bp = Blueprint("voice_bp", __name__)

# Final URL will be /api/voice/tts_with_visemes (and with trailing /)
@voice_bp.route("/tts_with_visemes", methods=["POST"])
@voice_bp.route("/tts_with_visemes/", methods=["POST"])
def tts_with_visemes():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    call_log.add("voice:request", "tts", text=text)

    if not text:
        return jsonify({"ok": False, "error": "No text to synthesize"}), 400

    audio_b64, visemes, err = synthesize_with_visemes(text)
    if err:
        current_app.logger.warning("TTS failed: %s", err)
        call_log.add("error", "tts_failed", error=err)
        return jsonify({"ok": False, "error": err, "audio_base64": None, "visemes": None}), 200

    call_log.add("voice:response", "tts_ok", size=len(audio_b64) if audio_b64 else 0)
    return jsonify({"ok": True, "audio_base64": audio_b64, "visemes": visemes})
