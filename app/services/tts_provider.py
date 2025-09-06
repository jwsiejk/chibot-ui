# app/services/tts_provider.py
import os
from typing import Protocol

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto")
    val = (val or "auto").strip().lower()
    if val in ("auto", ""):
        return "elevenlabs"
    return val

def load_tts_provider(name: str):
    if name == "elevenlabs":
        from .providers.elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS()
    raise RuntimeError(f"Unknown TTS provider: {name}")

def get_tts_provider(cfg: dict):
    return load_tts_provider(get_tts_provider_name(cfg))