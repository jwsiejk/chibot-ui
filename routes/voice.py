# routes/voice.py
from flask import Blueprint, request, jsonify, current_app
from services.tts_bridge import synthesize_with_visemes
from utils.call_log import call_log

voice_bp = Blueprint("voice_bp", __name__)

def _extract_text(data: dict) -> str:
    return (data.get("text")
            or data.get("input")
            or data.get("message")
            or data.get("utterance")
            or "").strip()

def _tts_impl():
    data = request.get_json(silent=True) or {}
    text = _extract_text(data)
    call_log.add("voice:request", "tts", text=text)
    if not text:
        return jsonify({"ok": False, "error": "No text to synthesize", "audio": None, "audio_base64": None, "visemes": None}), 400

    audio_b64, visemes, err = synthesize_with_visemes(text)
    if err:
        current_app.logger.warning("TTS failed: %s", err)
        call_log.add("error", "tts_failed", error=err)
        return jsonify({"ok": False, "error": err, "audio": None, "audio_base64": None, "visemes": None}), 200

    call_log.add("voice:response", "tts_ok", size=len(audio_b64) if audio_b64 else 0)
    return jsonify({
        "ok": True,
        "audio": audio_b64,        # alias expected by frontend
        "audio_base64": audio_b64, # backward compatibility
        "visemes": visemes,
        "mime": "audio/mpeg",
    })

_aliases = [
    "tts_with_visemes",
    "tts",
    "synthesize",
    "speak",
    "say",
    "eleven/tts",
    "eleven/speak",
]
for ix, path in enumerate(_aliases):
    voice_bp.add_url_rule(f"/{path}", endpoint=f"tts_{ix}", view_func=_tts_impl, methods=["POST"])
    voice_bp.add_url_rule(f"/{path}/", endpoint=f"tts_{ix}_slash", view_func=_tts_impl, methods=["POST"])

@voice_bp.route("/health", methods=["GET"])
def health():
    try:
        return jsonify({"ok": True, "configured": True})
    except Exception as e:
        return jsonify({"ok": False, "configured": False, "error": str(e)}), 200
