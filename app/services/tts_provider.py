
# app/services/tts_provider.py — Phase 0: no mocks, fail fast without vendor keys
import os
from typing import Protocol

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto")
    val = (val or "auto").strip().lower()
    if val in ("auto", "") or val == "elevenlabs":
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        raise RuntimeError("ELEVENLABS_API_KEY is not set — no mock TTS provider allowed.")
    raise RuntimeError(f"Unknown or disallowed TTS provider: {val}")

def load_tts_provider(name: str):
    if name == "elevenlabs":
        from .providers.elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS()
    raise RuntimeError(f"Unknown TTS provider: {name}")

def get_tts_provider(cfg: dict):
    return load_tts_provider(get_tts_provider_name(cfg))
