import os
import logging
import requests

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
ELEVEN_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

def tts_bytes(text: str, *, format: str = "mp3", voice_id: str | None = None) -> bytes:
    vid = (voice_id or ELEVEN_VOICE_ID).strip()
    if not ELEVEN_API_KEY or not vid:
        logging.warning("tts_bytes: ELEVENLABS not configured (missing API key or voice id)")
        return b""
    try:
        base = "https://api.elevenlabs.io/v1/text-to-speech"
        url = f"{base}/{vid}"
        headers = {
            "xi-api-key": ELEVEN_API_KEY,
            "accept": "audio/mpeg" if format == "mp3" else "audio/wav",
            "content-type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": ELEVEN_MODEL_ID,
            "voice_settings": {"stability": 0.35, "similarity_boost": 0.8},
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logging.warning(f"ElevenLabs TTS failed: {e}")
        return b""

def tts_with_visemes(text: str, *, format: str = "mp3", voice_id: str | None = None):
    audio = tts_bytes(text, format=format, voice_id=voice_id)
    visemes: list[dict] = []
    return audio, visemes
