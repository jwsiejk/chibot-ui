# app/services/llm_provider.py — Phase 7
import os, uuid
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def _allow_mock() -> bool:
    # Mocks allowed only when explicitly flagged and not in production
    if os.getenv("APP_ENV","").strip().lower() == "production":
        return False
    return bool(os.getenv("ALLOW_MOCK_PROVIDERS"))

def get_provider_name(cfg: dict) -> str:
    name = (cfg or {}).get("llm_provider", "auto")
    name = (name or "auto").strip().lower()
    if name in ("auto", ""):
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if _allow_mock():
            return "mock"
        raise RuntimeError("OPENAI_API_KEY missing and mock providers disallowed; set ALLOW_MOCK_PROVIDERS for non-production use.")
    if name == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for LLM provider 'openai'.")
        return "openai"
    if name == "mock":
        if not _allow_mock():
            raise RuntimeError("Mock LLM provider is disallowed in this environment.")
        return "mock"
    raise RuntimeError(f"Unknown or disallowed LLM provider: {name}")

def load_provider(name: str, cfg: dict | None = None):
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider
        from .vendor_clients import make_openai_client
        return OpenAIProvider(cfg or {}, client_factory=make_openai_client)
    if name == "mock":
        from .providers.mock_provider import MockProvider
        return MockProvider()
    # No other providers permitted
    raise RuntimeError(f"Unknown LLM provider: {name}")

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg), cfg=cfg or {})
