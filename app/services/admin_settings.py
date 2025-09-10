# app/services/admin_settings.py
from __future__ import annotations
import os, json, threading
from typing import Any, Dict

_LOCK = threading.Lock()
# Simple in-memory cache; in real app this persists in Neon (db). This file mirrors existing behavior.
_settings: Dict[str, Any] = {
    "confirm_ms": 420,
    "echo_threshold_boost": 1.9,
    "language_lock": "en",
    "min_speech_ms": 220,
    "nudge_delay_ms": 4200,
    "nudge_backoff_after_ignored": 2,
    "max_turn_seconds": 90,
    # New: Audio feature toggle exposed in Admin UI
    "feature_audio": os.environ.get("FEATURE_AUDIO", "true").lower() == "true",
    # TTS runtime tunables (non-secret) – keys remain in env only
    "tts_voice_id": os.environ.get("ELEVENLABS_VOICE_ID", ""),
    "tts_output_format": os.environ.get("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128"),
    "tts_model_id": os.environ.get("ELEVEN_MODEL_ID", "eleven_multilingual_v2"),
}

def get_settings() -> Dict[str, Any]:
    with _LOCK:
        return dict(_settings)

def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        for k,v in patch.items():
            if k in _settings:
                _settings[k] = v
        return dict(_settings)

def vendor_status() -> Dict[str, Any]:
    # Never expose secrets; just indicate presence
    return {
        "llm": "OpenAI",
        "stt": "Whisper",
        "tts": {
            "provider": "ElevenLabs",
            "key_present": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "voice_id_set": bool(os.environ.get("ELEVENLABS_VOICE_ID")),
            "output_format": os.environ.get("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128"),
        }
    }
