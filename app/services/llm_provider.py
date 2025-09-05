# app/services/llm_provider.py
from typing import Protocol, Dict, Any
import os

class LLMProvider(Protocol):
    def new_turn_id(self) -> str: ...
    def generate_reply(self, prompt: str, persona: Dict[str, Any] | None = None,
                       teacher_move: str | None = None, context: Dict[str, Any] | None = None) -> str: ...

# Registry of required env vars per provider for fail-fast validation
_REQUIRED_ENVS = {
    "openai_http": ["OPENAI_API_KEY"],
    # Add future providers here with their required envs
    # "anthropic_http": ["ANTHROPIC_API_KEY"],
    # "azure_openai_http": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
}

def _validate_provider_env(name: str):
    reqs = _REQUIRED_ENVS.get(name, [])
    missing = [k for k in reqs if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"LLM provider '{name}' missing required env(s): {', '.join(missing)}")

def get_provider_name(cfg: dict) -> str:
    """Resolve provider deterministically and fail fast if misconfigured.
    Rules:
      - If cfg.llm_provider is set, use it; otherwise default to 'openai_http'.
      - Validate required envs for the chosen provider; raise if missing.
      - 'mock' is allowed only if explicitly requested (e.g., tests).
    """
    val = (cfg or {}).get("llm_provider", "").strip().lower()
    name = (val if val not in ("auto",) else "") or "openai_http"

    if name == "mock":
        # Allowed only when explicitly requested (typically tests)
        return "mock"

    # Fail fast if provider is unknown
    known = set(_REQUIRED_ENVS.keys()) | {"mock"}
    if name not in known:
        raise RuntimeError(f"Unknown llm provider: {name}")

    _validate_provider_env(name)
    return name

def load_provider(name: str) -> LLMProvider:
    if name in ("openai_http", "openai"):
        from .providers_real.openai_http_provider import OpenAIHTTPProvider
        return OpenAIHTTPProvider()
    raise RuntimeError(f"Unknown llm provider: {name}")

def get_provider(cfg: dict) -> LLMProvider:
    return load_provider(get_provider_name(cfg))
