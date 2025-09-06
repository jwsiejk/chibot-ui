# app/services/llm_provider.py
import os
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def get_provider_name(cfg: dict) -> str:
    """
    No implicit fallback. Either configuration explicitly sets 'mock',
    or an OpenAI key is present (or a client is injected) to use 'openai'.
    """
    name = (cfg or {}).get("llm_provider", "auto")
    name = (name or "auto").strip().lower()
    if name == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        # No key: do not silently fall back.
        raise RuntimeError("No OPENAI_API_KEY; set llm_provider=mock explicitly for dev or provide a key.")
    return name

def load_provider(name: str, cfg: dict | None = None) -> LLMProvider:
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider
        from .vendor_clients import make_openai_client
        return OpenAIProvider(cfg or {}, client_factory=make_openai_client)
    if name == "mock":
        from .providers.mock_provider import MockProvider
        return MockProvider()
    raise RuntimeError(f"Unknown LLM provider: {name}")

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg), cfg=cfg or {})
