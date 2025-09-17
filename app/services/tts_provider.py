
# app/services/tts_provider.py — Phase 1: support mock provider in CI/offline
import os
from typing import Protocol

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto")
    val = (val or "auto").strip().lower()
    use_mock = bool(os.environ.get("USE_MOCK_VENDORS") or os.environ.get("CI_FAST"))
    if use_mock:
        return "mock"
    if val in ("auto", "") or val == "elevenlabs":
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        # Offline path: if we reach here without a key and mock not allowed, fail
        raise RuntimeError("ELEVENLABS_API_KEY is not set — no mock TTS provider allowed.")
    if val == "mock":
        return "mock"
    raise RuntimeError(f"Unknown or disallowed TTS provider: {val}")

def load_tts_provider(name: str):
    if name == "elevenlabs":
        from .providers.elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS()
    if name == "mock":
        from .providers.mock_tts import MockTTS
        return MockTTS()
    raise RuntimeError(f"Unknown TTS provider: {name}")

def get_tts_provider(cfg: dict):
    return load_tts_provider(get_tts_provider_name(cfg))
