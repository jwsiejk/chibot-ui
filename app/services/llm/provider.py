"""Provider factory helpers for LLM integrations."""

from __future__ import annotations

from typing import Optional

from app.services.llm.openai_client import OpenAILLMProvider


def create_from_env(*, telemetry_bus=None, clock=None) -> Optional[OpenAILLMProvider]:
    """Instantiate the default provider when the runtime is configured."""

    kwargs = {}
    if telemetry_bus is not None:
        kwargs["telemetry_bus"] = telemetry_bus
    if clock is not None:
        kwargs["clock"] = clock

    provider = OpenAILLMProvider(**kwargs)
    return provider if provider.is_configured else None


__all__ = ["create_from_env"]
