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
    """Text-to-speech endpoint.
    Accepts JSON like { text, voice_id?, model? } and returns
    { ok, audio_base64, audio, visemes?, marks? }.
    Always flattens any non-string `text` to a string so a Python generator
    can never leak through as "<generator object ...>".
    """
    data = request.get_json(silent=True) or {}
    # 1) Get text from payload and *guarantee* it is a plain string
    text_raw = _extract_text(data)
    text = ensure_text(text_raw)
    try:
        safe_preview = (text[:200] + "…") if len(text) > 200 else text
        call_log.add("voice:request", "tts", text=safe_preview)
    except Exception:
        pass

    if not text:
        return jsonify({"ok": False, "error": "empty_text"}), 200

    # Optional voice/model overrides
    try:
        voice_id = (data.get("voice_id") or data.get("voice") or "").strip()
    except Exception:
        voice_id = ""
    try:
        model = (data.get("model") or data.get("tts_model") or "").strip()
    except Exception:
        model = ""

    # 2) Synthesize
    audio_b64, visemes, err = synthesize_with_visemes(text)
    if err or not audio_b64:
        try:
            call_log.add("voice:response", "tts_error", error=str(err or "unknown"))
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(err or "tts_failed")}), 200

    try:
        call_log.add("voice:response", "tts_ok", size=len(audio_b64))
    except Exception:
        pass

    resp = {
        "ok": True,
        "audio_base64": audio_b64,  # canonical
        # compatibility for clients expecting `audio`
        "audio": audio_b64,
    }
    if visemes:
        resp["visemes"] = visemes
        resp["marks"] = visemes  # compatibility alias

    return jsonify(resp)

# Multiple aliases so different frontends work without changes
_aliases = [
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
