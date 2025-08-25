import os
import logging
import requests

def _get_env(name: str, *aliases: str) -> str:
    val = os.getenv(name)
    if val and val.strip():
        return val.strip()
    for a in aliases:
        v = os.getenv(a)
        if v and v.strip():
            return v.strip()
    return ""

def _cfg():
    api_key = _get_env("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
    voice_id = _get_env("ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID", "CHIP_VOICE_ID")
    model_id = _get_env("ELEVENLABS_MODEL_ID", "ELEVEN_MODEL_ID") or "eleven_turbo_v2_5"
    return api_key, voice_id, model_id

def _is_placeholder(v: str) -> bool:
    v = (v or '').strip()
    return (not v) or v.lower() in {'your_voice_id','voice_id'} or len(v) < 8

def tts_bytes(text: str, *, format: str = "mp3", voice_id: str | None = None) -> bytes:
    api_key, default_voice, model_id = _cfg()
    vid = (voice_id or default_voice or "").strip()
    if not api_key or not vid:
        logging.warning("tts_bytes: ELEVENLABS not configured (missing API key or voice id)")
        return b""
    try:
        base = "https://api.elevenlabs.io/v1/text-to-speech"
        url = f"{base}/{vid}"
        headers = {
            "xi-api-key": api_key,
            "accept": "audio/mpeg" if format == "mp3" else "audio/wav",
            "content-type": "application/json",
        }
        payload = {
            "text": text or "",
            "model_id": model_id,
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
    visemes: list[dict] = []  # if you wire timestamps later, populate this
    return audio, visemes