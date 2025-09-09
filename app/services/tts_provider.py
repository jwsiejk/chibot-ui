
# app/services/tts_provider.py
import os
from typing import Protocol

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto")
    val = (val or "auto").strip().lower()
    # NEVER mock in production. Only allow mock in CI_FAST.
    if val in ("auto", ""):
        if os.environ.get("CI_FAST"):
            return "mock"
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        raise RuntimeError("ELEVENLABS_API_KEY is not set — refusing to run with mock in production.")
    if val == "mock":
        if os.environ.get("CI_FAST"):
            return "mock"
        raise RuntimeError("Mock TTS provider is disallowed outside CI_FAST.")
    return val

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
