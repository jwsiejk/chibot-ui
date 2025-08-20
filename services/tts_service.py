import os, base64, requests
from .viseme_service import visemes_for_text

ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID")
ELEVEN_MODEL_ID = os.getenv("ELEVEN_MODEL_ID", "eleven_multilingual_v2")

def tts_bytes(text: str):
    if not (ELEVEN_API_KEY and ELEVEN_VOICE_ID):
        return None, "ElevenLabs not configured"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.7},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"ElevenLabs error: {r.status_code}"
        return r.content, None
    except Exception as e:
        return None, str(e)

def tts_with_visemes(text: str):
    audio, err = tts_bytes(text)
    schedule = visemes_for_text(text)
    if not audio:
        return {"audio": None, "visemes": schedule, "relative": True}, err or "TTS unavailable"
    b64 = base64.b64encode(audio).decode("utf-8")
    return {"audio": b64, "visemes": schedule, "relative": True}, None
