# app/services/stt_provider.py — Vendors only (Deepgram)
import os
from typing import Protocol

class STTProvider(Protocol):
    def transcribe(self, audio_bytes: bytes, *, language: str = "en") -> str: ...

def get_stt_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("stt_provider", "auto")
    val = (val or "auto").strip().lower()
    if val in ("auto", "", "deepgram"):
        if os.environ.get("DEEPGRAM_API_KEY"):
            return "deepgram"
        raise RuntimeError("DEEPGRAM_API_KEY missing; cannot use STT without vendor credentials.")
    if val == "deepgram":
        if not os.environ.get("DEEPGRAM_API_KEY"):
            raise RuntimeError("DEEPGRAM_API_KEY is required for STT provider 'deepgram'.")
        return "deepgram"
    raise RuntimeError(f"Unknown or disallowed STT provider: {val}")

def load_stt_provider(name: str):
    if name == "deepgram":
        from .providers.deepgram_stt import DeepgramSTT
        return DeepgramSTT()
    raise RuntimeError(f"Unknown STT provider: {name}")

def get_stt_provider(cfg: dict):
    return load_stt_provider(get_stt_provider_name(cfg))
