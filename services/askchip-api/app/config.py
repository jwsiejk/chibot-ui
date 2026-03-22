from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = '127.0.0.1'
    port: int = 8000
    app_name: str = 'AskChip Local API'
    database_path: Path = Path('data/askchip_local.db')
    ollama_base_url: str = 'http://127.0.0.1:11434'
    ollama_model: str = 'llama3.2:3b'
    ollama_timeout_seconds: float = 60.0
    prompt_transcript_window: int = 6
    stt_model: str = 'base'
    stt_device: str = 'auto'
    stt_compute_type: str = 'int8'
    stt_cpu_threads: int = 4
    tts_voice: str = 'af_heart'
    tts_device: str = 'cpu'
    tts_model_path: Path | None = None
    tts_voices_path: Path | None = None
    tts_sample_rate_hz: int = 24000
    tts_speed: float = 1.0
    tts_lang_code: str = 'a'
    ollama_warmup_enabled: bool = True
    tts_warmup_enabled: bool = False


_ENV_MAP: dict[str, tuple[str, callable]] = {
    'host': ('ASKCHIP_API_HOST', str),
    'port': ('ASKCHIP_API_PORT', int),
    'app_name': ('ASKCHIP_API_NAME', str),
    'database_path': ('ASKCHIP_API_DATABASE_PATH', Path),
    'ollama_base_url': ('OLLAMA_BASE_URL', str),
    'ollama_model': ('OLLAMA_MODEL', str),
    'ollama_timeout_seconds': ('OLLAMA_TIMEOUT_SECONDS', float),
    'prompt_transcript_window': ('ASKCHIP_PROMPT_TRANSCRIPT_WINDOW', int),
    'stt_model': ('ASKCHIP_STT_MODEL', str),
    'stt_device': ('ASKCHIP_STT_DEVICE', str),
    'stt_compute_type': ('ASKCHIP_STT_COMPUTE_TYPE', str),
    'stt_cpu_threads': ('ASKCHIP_STT_CPU_THREADS', int),
    'tts_voice': ('ASKCHIP_TTS_VOICE', str),
    'tts_device': ('ASKCHIP_TTS_DEVICE', str),
    'tts_model_path': ('ASKCHIP_TTS_MODEL_PATH', Path),
    'tts_voices_path': ('ASKCHIP_TTS_VOICES_PATH', Path),
    'tts_sample_rate_hz': ('ASKCHIP_TTS_SAMPLE_RATE_HZ', int),
    'tts_speed': ('ASKCHIP_TTS_SPEED', float),
    'tts_lang_code': ('ASKCHIP_TTS_LANG_CODE', str),
    'ollama_warmup_enabled': ('ASKCHIP_OLLAMA_WARMUP_ENABLED', lambda value: value.lower() in {'1', 'true', 'yes', 'on'}),
    'tts_warmup_enabled': ('ASKCHIP_TTS_WARMUP_ENABLED', lambda value: value.lower() in {'1', 'true', 'yes', 'on'}),
}


def load_settings() -> Settings:
    values = {}
    for field, (env_name, caster) in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is None or raw == '':
            continue
        values[field] = caster(raw)
    return Settings(**values)


settings = load_settings()
