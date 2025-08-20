# services/tts_service.py
import os
import logging
import requests

# ENV
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # default voice
ELEVEN_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")

def tts_bytes(text: str, *, format: str = "mp3", provider: str | None = None,
              voice_id: str | None = None) -> bytes:
    """
    Return audio bytes for `text`.
    - provider: "elevenlabs" or "openai" (auto-picked if None)
    - format: "mp3" (default) or "wav" (OpenAI supports mp3; ElevenLabs supports both)
    """
    prov = provider or ("elevenlabs" if ELEVEN_API_KEY else "openai")

    # Prefer ElevenLabs when available (gives best latency & future viseme support).
    if prov == "elevenlabs" and ELEVEN_API_KEY:
        try:
            return _elevenlabs_tts_bytes(text, voice_id=voice_id or ELEVEN_VOICE_ID, fmt=format)
        except Exception as e:
            logging.warning(f"ElevenLabs TTS failed: {e}. Falling back to OpenAI.")
            # Fall through to OpenAI

    if OPENAI_API_KEY:
        try:
            return _openai_tts_bytes(text, fmt="mp3")  # OpenAI returns mp3 reliably
        except Exception as e:
            logging.warning(f"OpenAI TTS failed: {e}. Returning empty audio.")
            return b""

    # No provider configured
    return b""


def tts_with_visemes(text: str, *, format: str = "mp3",
                     provider: str | None = None, voice_id: str | None = None) -> tuple[bytes, list]:
    """
    Return (audio_bytes, visemes).
    - Currently returns real audio; visemes=[] placeholder unless you wire up ElevenLabs streaming.
    """
    audio = tts_bytes(text, format=format, provider=provider, voice_id=voice_id)
    # Placeholder: to produce real visemes, switch to ElevenLabs streaming (WebSocket) and
    # capture viseme events. Your frontend already handles visemes=[] gracefully.
    visemes: list[dict] = []
    return audio, visemes


# -------------------------
# Providers
# -------------------------

def _elevenlabs_tts_bytes(text: str, *, voice_id: str, fmt: str = "mp3") -> bytes:
    """
    ElevenLabs REST (non-streaming) TTS -> audio bytes.
    Requires ELEVENLABS_API_KEY. Uses requests only.
    """
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
        # Tweakable defaults; safe, not too robotic
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.8},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.content


def _openai_tts_bytes(text: str, *, fmt: str = "mp3") -> bytes:
    """
    OpenAI TTS -> audio bytes (mp3). Uses streaming API to always return raw bytes.
    """
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    # The streaming helper returns a byte stream you can .read()
    try:
        with client.audio.speech.with_streaming_response.create(
            model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=text, format=fmt
        ) as response:
            return response.read()
    except TypeError:
        # Older/newer SDK fallbacks without 'format' kw or different return
        with client.audio.speech.with_streaming_response.create(
            model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=text
        ) as response:
            return response.read()
