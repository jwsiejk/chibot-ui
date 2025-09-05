
# app/services/llm_provider.py
from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

def get_provider_name(cfg: dict) -> str:
    return (cfg or {}).get("llm_provider", "mock").strip().lower() or "mock"

def load_provider(name: str) -> LLMProvider:
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    # default mock
    from .providers.mock_provider import MockProvider
    return MockProvider()

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg))
