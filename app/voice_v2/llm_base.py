"""Abstract LLM provider base built on the shared circuit breaker plumbing."""
from __future__ import annotations

import abc

from app.telemetry import bus

from .asr_base import (
    EVT_PROVIDER_CLOSE,
    EVT_PROVIDER_OPEN,
    EVT_PROVIDER_TRIP,
    ClockFn,
    ProviderAdapterBase,
    ProviderCircuitBreaker,
    ProviderCircuitOpenError,
    ProviderTimeoutError,
)


class LLMProviderBase(ProviderAdapterBase):
    """Base class for LLM providers with retry and breaker semantics."""

    def __init__(
        self,
        *,
        vendor: str,
        telemetry_bus=bus,
        retries: int = 1,
        timeout_s: float = 10.0,
        clock: ClockFn | None = None,
    ) -> None:
        super().__init__(
            vendor=vendor,
            provider_type="llm",
            telemetry_bus=telemetry_bus,
            retries=retries,
            timeout_s=timeout_s,
            clock=clock,
        )

    async def generate(self, *args, **kwargs) -> object:
        """Execute the LLM request with retry and breaker protections."""

        return await self._invoke_with_breaker(self._generate_impl, *args, **kwargs)

    @abc.abstractmethod
    async def _generate_impl(self, *args, **kwargs) -> object:
        """Concrete providers must implement this coroutine."""


__all__ = [
    "LLMProviderBase",
    "EVT_PROVIDER_CLOSE",
    "EVT_PROVIDER_OPEN",
    "EVT_PROVIDER_TRIP",
    "ProviderCircuitBreaker",
    "ProviderCircuitOpenError",
    "ProviderTimeoutError",
]
