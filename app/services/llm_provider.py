
# app/services/llm_provider.py
import os
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def get_provider_name(cfg: dict) -> str:
    name = (cfg or {}).get("llm_provider", "auto")
    name = (name or "auto").strip().lower()
    # NEVER mock in production. Only allow mock in CI_FAST.
    if name in ("auto", ""):
        if os.environ.get("CI_FAST"):
            return "mock"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        raise RuntimeError("OPENAI_API_KEY is not set — refusing to run with mock in production.")
    if name == "mock":
        if os.environ.get("CI_FAST"):
            return "mock"
        raise RuntimeError("Mock LLM provider is disallowed outside CI_FAST.")
    return name

def load_provider(name: str, cfg: dict | None = None):
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
