
# app/services/tts_provider.py
from typing import Protocol, Tuple, Dict, Any

class TTSProvider(Protocol):
    def synth(self, text: str, *, voice_id: str | None = None, format: str | None = None) -> tuple[bytes, list[dict]]: ...

def get_tts_provider_name(cfg: dict) -> str:
    val = (cfg or {}).get("tts_provider", "auto").strip().lower()
    if val in ("auto","",None):
        import os
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs"
        raise RuntimeError("TTS provider not configured: set ELEVENLABS_API_KEY or cfg.tts_provider")
    return val

def load_tts_provider(name: str):
    if name == "elevenlabs":
        from .providers.elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS()
    from .providers.mock_tts import MockTTS
    return MockTTS()

def get_tts_provider(cfg: dict):
    return load_tts_provider(get_tts_provider_name(cfg))
