from __future__ import annotations

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


settings = Settings()
