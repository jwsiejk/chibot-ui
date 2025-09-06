# app/services/tts_provider.py
import os
from typing import Protocol

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def _env_is_prod() -> bool:
    return (os.getenv("APP_ENV","").lower() in ("prod","production") or
            os.getenv("ENV","").lower() in ("prod","production"))

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto").strip().lower()
    if val in ("auto","",None):
        has_key = bool(os.environ.get("ELEVENLABS_API_KEY"))
        if has_key:
            return "elevenlabs"
        allow_mock = os.getenv("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
        if _env_is_prod() or not allow_mock:
            raise RuntimeError("No ELEVENLABS_API_KEY and mocks are disallowed in this environment.")
        return "mock"
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
