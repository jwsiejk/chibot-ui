# services/tts_service.py
import os
import logging
import requests
from typing import Tuple, List

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # replace with your Chip voice ID
ELEVEN_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")  # fallback voice (not Chip)

def tts_bytes(text: str, *, format: str = "mp3", provider: str | None = None, voice_id: str | None = None) -> bytes:
    prov = provider or ("elevenlabs" if ELEVEN_API_KEY else ("openai" if OPENAI_API_KEY else None))
    if prov == "elevenlabs" and ELEVEN_API_KEY:
        try:
            return _elevenlabs_tts_bytes(text, voice_id=(voice_id or ELEVEN_VOICE_ID), fmt=format)
        except Exception as e:
            logging.warning(f"ElevenLabs TTS failed: {e}; falling back to OpenAI.")
            prov = "openai"
    if prov == "openai" and OPENAI_API_KEY:
        try:
            return _openai_tts_bytes(text, fmt="mp3")
        except Exception as e:
            logging.warning(f"OpenAI TTS failed: {e}.")
            return b""
    logging.warning("No TTS provider configured; returning empty audio.")
    return b""

def tts_with_visemes(text: str, *, format: str = "mp3", provider: str | None = None, voice_id: str | None = None) -> Tuple[bytes, List[dict]]:
    audio = tts_bytes(text, format=format, provider=provider, voice_id=voice_id)
    # Placeholder: real visemes require ElevenLabs streaming; frontend tolerates []
    return audio, []

def _elevenlabs_tts_bytes(text: str, *, voice_id: str, fmt: str = "mp3") -> bytes:
    base = "https://api.elevenlabs.io/v1/text-to-speech"
    url = f"{base}/{voice_id}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "accept": "audio/mpeg" if fmt == "mp3" else "audio/wav",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.85},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.content

def _openai_tts_bytes(text: str, *, fmt: str = "mp3") -> bytes:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        with client.audio.speech.with_streaming_response.create(
            model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=text, format=fmt
        ) as response:
            return response.read()
    except TypeError:
        with client.audio.speech.with_streaming_response.create(
            model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=text
        ) as response:
            return response.read()
