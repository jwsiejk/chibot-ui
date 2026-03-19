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


_ENV_MAP: dict[str, tuple[str, callable]] = {
    'host': ('ASKCHIP_API_HOST', str),
    'port': ('ASKCHIP_API_PORT', int),
    'app_name': ('ASKCHIP_API_NAME', str),
    'database_path': ('ASKCHIP_API_DATABASE_PATH', Path),
    'ollama_base_url': ('OLLAMA_BASE_URL', str),
    'ollama_model': ('OLLAMA_MODEL', str),
    'ollama_timeout_seconds': ('OLLAMA_TIMEOUT_SECONDS', float),
    'prompt_transcript_window': ('ASKCHIP_PROMPT_TRANSCRIPT_WINDOW', int),
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
