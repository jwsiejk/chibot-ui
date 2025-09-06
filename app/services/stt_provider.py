# app/services/stt_provider.py
import os
from typing import Protocol

class STTProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str: ...

def get_stt_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("stt_provider", "auto")
    val = (val or "auto").strip().lower()
    if val in ("auto", ""):
        return "whisper"
    return val

def load_stt_provider(name: str):
    if name == "whisper":
        from .providers.whisper_stt import WhisperSTT
        return WhisperSTT()
    raise RuntimeError(f"Unknown STT provider: {name}")

def get_stt_provider(cfg: dict):
    return load_stt_provider(get_stt_provider_name(cfg))