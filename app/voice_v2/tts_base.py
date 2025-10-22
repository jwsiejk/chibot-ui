"""Abstract TTS provider base leveraging the shared circuit breaker."""
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


class TTSProviderBase(ProviderAdapterBase):
    """Base class for TTS providers with retry and breaker behaviour."""

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
            provider_type="tts",
            telemetry_bus=telemetry_bus,
            retries=retries,
            timeout_s=timeout_s,
            clock=clock,
        )

    async def synthesize(self, *args, **kwargs) -> object:
        """Execute synthesis with retry and breaker protections."""

        return await self._invoke_with_breaker(self._synthesize_impl, *args, **kwargs)

    @abc.abstractmethod
    async def _synthesize_impl(self, *args, **kwargs) -> object:
        """Concrete providers must implement this coroutine."""


__all__ = [
    "TTSProviderBase",
    "EVT_PROVIDER_CLOSE",
    "EVT_PROVIDER_OPEN",
    "EVT_PROVIDER_TRIP",
    "ProviderCircuitBreaker",
    "ProviderCircuitOpenError",
    "ProviderTimeoutError",
]
