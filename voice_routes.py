# voice_routes.py — greeting, generic TTS, and optional transcription
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

# DB helpers
from memory import get_user, log_conversation

# OpenAI (for STT or LLM-crafted alt text, if needed)
from openai import OpenAI
_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ElevenLabs TTS
import requests

_ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
_ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "Rachel")  # set to your Chip voice ID
_ELEVEN_BASE = "https://api.elevenlabs.io/v1/text-to-speech"

WORD_LIMIT = int(os.getenv("CHAT_WORD_LIMIT", "30"))  # keep parity with chat

voice_bp = Blueprint("voice", __name__)


def _limit_words(text: str, max_words: int = WORD_LIMIT) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return text or ""
    return " ".join(words[:max_words]).rstrip(",.;:!?")


def _tts_generate_mp3(text: str) -> str:
    """
    Generate TTS via ElevenLabs and save to /static/audio/<uuid>.mp3
    Returns a web path like "/static/audio/xxx.mp3".
    """
    if not _ELEVEN_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    audio_dir = Path(current_app.static_folder, "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    outfile = audio_dir / f"{uuid.uuid4().hex}.mp3"

    url = f"{_ELEVEN_BASE}/{_ELEVEN_VOICE_ID}"
    headers = {
        "xi-api-key": _ELEVEN_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2"),
        "voice_settings": {
            "stability": float(os.getenv("ELEVEN_STABILITY", "0.4")),
            "similarity_boost": float(os.getenv("ELEVEN_SIMILARITY", "0.8")),
        },
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    with open(outfile, "wb") as f:
        f.write(r.content)

    return f"/static/audio/{outfile.name}"


@voice_bp.post("/greet")
def greet():
    """
    Dynamic greeting used by the UI when a session starts.
    Request JSON: { email? }
    Response: { audio: "/static/audio/xxx.mp3" | null, reply: "text" }
    """
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or request.headers.get("X-User-Email") or "").strip() or None
    user = get_user(email) if email else {}

    name = (user or {}).get("name") or "there"
    title = (user or {}).get("title") or ""

    # Keep it natural and short; 30-word guardrail applied.
    greeting_text = f"Hey {name}! Ready to talk Pure Storage? What do you want to tackle first?"
    greeting_text = _limit_words(greeting_text, WORD_LIMIT)

    audio_path = None
    try:
        audio_path = _tts_generate_mp3(greeting_text)
    except Exception as e:
        current_app.logger.warning(f"TTS generation failed: {e}")

    # Log server-side
    if email:
        log_conversation(email, "assistant", greeting_text, meta={"greet": True, "t": datetime.utcnow().isoformat()})

    return jsonify({"audio": audio_path, "reply": greeting_text})


@voice_bp.post("/api/voice/tts")
def api_voice_tts():
    """
    Generic TTS for any reply text the UI wants spoken.
    Request JSON: { text }
    Response: { audio: "/static/audio/xxx.mp3" }
    """
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    text = _limit_words(text, WORD_LIMIT)
    try:
        audio_path = _tts_generate_mp3(text)
        return jsonify({"audio": audio_path})
    except Exception as e:
        current_app.logger.exception("api_voice_tts failed")
        return jsonify({"error": "tts_failed", "detail": str(e)}), 500


@voice_bp.post("/api/voice/transcribe")
def api_voice_transcribe():
    """
    Optional: transcribe uploaded audio (form-data: file=blob).
    Response: { text }
    """
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "empty filename"}), 400

    try:
        # Save temp file (OpenAI needs a named file-like)
        uploads = Path(current_app.static_folder, "uploads")
        uploads.mkdir(parents=True, exist_ok=True)
        tmp = uploads / f"{uuid.uuid4().hex}-{f.filename}"
        f.save(tmp)

        with open(tmp, "rb") as audio_file:
            transcript = _openai.audio.transcriptions.create(
                model=os.getenv("WHISPER_MODEL", "gpt-4o-transcribe"),
                file=audio_file,
            )
        text = (getattr(transcript, "text", None) or "").strip()
        return jsonify({"text": text})
    except Exception as e:
        current_app.logger.exception("api_voice_transcribe failed")
        return jsonify({"error": "transcribe_failed", "detail": str(e)}), 500
