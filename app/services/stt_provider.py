# app/services/stt_provider.py
import os
from typing import Protocol

class STTProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str: ...

def _env_is_prod() -> bool:
    return (os.getenv("APP_ENV","").lower() in ("prod","production") or
            os.getenv("ENV","").lower() in ("prod","production"))

def get_stt_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("stt_provider", "auto").strip().lower()
    if val in ("auto","",None):
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        if has_key:
            return "whisper"
        allow_mock = os.getenv("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
        if _env_is_prod() or not allow_mock:
            raise RuntimeError("No OPENAI_API_KEY and mocks are disallowed in this environment.")
        return "mock"
    return val

def load_stt_provider(name: str):
    if name == "whisper":
        from .providers.whisper_stt import WhisperSTT
        return WhisperSTT()
    if name == "mock":
        from .providers.mock_stt import MockSTT
        return MockSTT()
    raise RuntimeError(f"Unknown STT provider: {name}")

def get_stt_provider(cfg: dict):
    return load_stt_provider(get_stt_provider_name(cfg))
