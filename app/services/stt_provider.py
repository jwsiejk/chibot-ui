
# app/services/stt_provider.py
from typing import Protocol

class STTProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str: ...

def get_stt_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("stt_provider", "auto").strip().lower()
    if val in ("auto","",None):
        import os
        return "whisper" if os.environ.get("OPENAI_API_KEY") else "mock"
    return val

def load_stt_provider(name: str):
    if name == "whisper":
        from .providers.whisper_stt import WhisperSTT
        return WhisperSTT()
    from .providers.mock_stt import MockSTT
    return MockSTT()

def get_stt_provider(cfg: dict):
    return load_stt_provider(get_stt_provider_name(cfg))
