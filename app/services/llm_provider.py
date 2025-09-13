
# app/services/llm_provider.py — Phase 0: no mocks, fail fast without vendor keys
import os
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def get_provider_name(cfg: dict) -> str:
    name = (cfg or {}).get("llm_provider", "auto")
    name = (name or "auto").strip().lower()
    if name in ("auto", ""):
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        raise RuntimeError("OPENAI_API_KEY is not set — no mock provider allowed.")
    if name == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for LLM provider 'openai'.")
        return "openai"
    raise RuntimeError(f"Unknown or disallowed LLM provider: {name}")

def load_provider(name: str, cfg: dict | None = None):
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider
        from .vendor_clients import make_openai_client
        return OpenAIProvider(cfg or {}, client_factory=make_openai_client)
    # No other providers permitted
    raise RuntimeError(f"Unknown LLM provider: {name}")

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg), cfg=cfg or {})
