# services/tts_service.py
import os
import logging
import base64
import requests
from typing import Tuple, List, Optional

_PLACEHOLDERS = {"YOUR_VOICE_ID", "CHANGE-ME", "CHANGEME", "NONE", "NULL", "PLACEHOLDER"}

def _first_nonplaceholder(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if s.upper() in _PLACEHOLDERS:
            continue
        return s
    return ""

def _cfg() -> dict:
    api_key  = _first_nonplaceholder("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "XI_API_KEY")
    # Prefer CHIP_VOICE_ID (your Render env), then both common variants:
    voice_id = _first_nonplaceholder("CHIP_VOICE_ID", "ELEVENLABS_VOICE_ID", "ELEVEN_VOICE_ID")
    model_id = _first_nonplaceholder("ELEVENLABS_MODEL_ID", "ELEVEN_MODEL_ID") or "eleven_turbo_v2"
    fmt      = _first_nonplaceholder("ELEVEN_OUTPUT_FORMAT", "ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_128"
    return {"api_key": api_key, "voice_id": voice_id, "model_id": model_id, "fmt": fmt}

def _validate_cfg(cfg: dict) -> Optional[str]:
    if not cfg.get("api_key"):
        return "Missing ELEVENLABS_API_KEY"
    if not cfg.get("voice_id"):
        return "Missing ElevenLabs voice id (set CHIP_VOICE_ID or ELEVENLABS_VOICE_ID)"
    return None

def tts_bytes(text: str, *, format: str = "mp3", voice_id: Optional[str] = None):
    """Return raw audio bytes OR (bytes, error) tuple for compatibility."""
    text = (text or "").strip()
    if not text:
        return b"", "No text"
    cfg = _cfg()
    if voice_id:
        cfg["voice_id"] = voice_id.strip()
    err = _validate_cfg(cfg)
    if err:
        logging.warning("ElevenLabs TTS config error: %s", err)
        return b"", err

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}/stream"
    headers = {
        "xi-api-key": cfg["api_key"],
        "Accept": "audio/mpeg" if (format or "mp3").lower().startswith("mp3") else "audio/wav",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": cfg["model_id"],
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.8},
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            # try to parse a helpful error
            try:
                j = r.json()
                msg = j.get("detail") or j.get("error") or str(j)
            except Exception:
                msg = r.text[:300]
            raise RuntimeError(f"ElevenLabs error {r.status_code}: {msg}")
        return r.content
    except Exception as e:
        logging.warning("ElevenLabs TTS failed: %s", e)
        return b"", str(e)

def tts_with_visemes(text: str, *, format: str = "mp3", voice_id: Optional[str] = None):
    audio = tts_bytes(text, format=format, voice_id=voice_id)
    # Back-compat: tts_bytes may return (bytes, err) or just bytes
    err = None
    if isinstance(audio, tuple) and len(audio) == 2:
        audio, err = audio
    if not isinstance(audio, (bytes, bytearray)):
        return (b"", []), (err or "TTS failed")
    visemes: List[dict] = []
    return (audio, visemes)
