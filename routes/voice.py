# routes/voice.py
from __future__ import annotations
from flask import Blueprint, request, jsonify, current_app
from services.tts_bridge import synthesize_with_visemes
from utils.call_log import call_log
from utils.text import ensure_text

voice_bp = Blueprint("voice_bp", __name__)

def _extract_text(data: dict) -> str:
    return (data.get("text")
            or data.get("input")
            or data.get("message")
            or data.get("utterance")
            or "").strip()

def _tts_impl():
    """Accepts JSON {text} and returns { ok, audio, visemes?, relative }.
    Kept intentionally permissive so different front‑ends can call it.
    """
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    # Normalize/collect any iterable/generator input to a string
    text_raw = _extract_text(payload)
    text = ensure_text(text_raw)

    call_log.add("voice:request", "tts", size=len(text))

    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 200

    audio_b64, visemes, err = synthesize_with_visemes(text)
    if err or not audio_b64:
        # Log but do not fail hard – the client will show a helpful status
        call_log.add("voice:error", "tts_failed", error=str(err or "no_audio"), size=len(text))
        return jsonify({"ok": False, "error": str(err or "no_audio")}), 200

    resp = {"ok": True, "audio": audio_b64}
    if visemes:
        resp["visemes"] = visemes
        resp["relative"] = True

    call_log.add("voice:response", "tts_ok", audio=len(audio_b64))
    return jsonify(resp)

# Multiple aliases – keep legacy names so older UIs don’t 404
_aliases = [
    "tts_with_visemes",
    "tts",
    "speak",
    "eleven/tts",
    "eleven/speak",
]
for ix, path in enumerate(_aliases):
    voice_bp.add_url_rule(f"/{path}", endpoint=f"tts_{ix}", view_func=_tts_impl, methods=["POST"])
    voice_bp.add_url_rule(f"/{path}/", endpoint=f"tts_{ix}_slash", view_func=_tts_impl, methods=["POST"])

@voice_bp.route("/health", methods=["GET"])
def health():
    try:
        configured = True
        return jsonify({"ok": True, "configured": configured})
    except Exception as e:
        return jsonify({"ok": False, "configured": False, "error": str(e)}), 200
