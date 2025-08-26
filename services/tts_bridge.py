# services/tts_bridge.py
import base64
import os
import requests

# Read env vars (both common names supported)
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
ELEVEN_VOICE_ID = (
    os.getenv("ELEVENLABS_VOICE_ID")
    or os.getenv("ELEVEN_VOICE_ID")
    or os.getenv("CHIP_VOICE_ID")  # alias for Render env
    or ""
).strip()
ELEVEN_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2")

def synthesize_with_visemes(text: str):
    """
    Returns (audio_base64, visemes, error_msg).
    - audio_base64: str on success, else None
    - visemes: None for now (hook it up later if desired)
    - error_msg: str on failure, else None
    """
    text = (text or "").strip()
    if not text:
        return None, None, "No text provided"

    if not ELEVEN_API_KEY:
        return None, None, "Missing ELEVENLABS_API_KEY"
    if not ELEVEN_VOICE_ID or ELEVEN_VOICE_ID.upper().startswith("YOUR_"):
        return None, None, "Missing or placeholder ELEVENLABS_VOICE_ID"

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}/stream"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            # Try to surface a concise error message
            try:
                err_json = r.json()
                msg = err_json.get("detail") or err_json.get("error") or err_json
            except Exception:
                msg = r.text[:300]
            return None, None, f"ElevenLabs error {r.status_code}: {msg}"

        audio_b64 = base64.b64encode(r.content).decode("utf-8")
        return audio_b64, None, None
    except Exception as e:
        return None, None, f"TTS exception: {e}"
