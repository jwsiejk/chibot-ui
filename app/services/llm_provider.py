# app/services/llm_provider.py
import os
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def _env_is_prod() -> bool:
    return (os.getenv("APP_ENV","").lower() in ("prod","production") or
            os.getenv("ENV","").lower() in ("prod","production"))

def get_provider_name(cfg: dict) -> str:
    name = (cfg or {}).get("llm_provider", "auto")
    name = (name or "auto").strip().lower()
    if name == "auto":
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        if has_key:
            return "openai"
        # No key. In production (or unless explicitly allowed), do not fall back to mock.
        allow_mock = os.getenv("ALLOW_MOCK_PROVIDERS","false").lower() in ("1","true","yes")
        if _env_is_prod() or not allow_mock:
            raise RuntimeError("No OPENAI_API_KEY and mocks are disallowed in this environment.")
        return "mock"
    return name

def load_provider(name: str) -> LLMProvider:
    if name == "openai":
        from .providers_real.openai_http_provider import OpenAIHTTPProvider as OpenAIProvider
        return OpenAIProvider()
    if name == "mock":
        from .providers.mock_provider import MockProvider
        return MockProvider()
    raise RuntimeError(f"Unknown LLM provider: {name}")

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg))
