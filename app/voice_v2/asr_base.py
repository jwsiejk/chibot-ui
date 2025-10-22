"""Abstract ASR provider base with retry and circuit breaker support."""
from __future__ import annotations

import abc
import asyncio
import inspect
import time
from collections import deque
from typing import Awaitable, Callable, Deque, Optional

from app.telemetry import bus

EVT_PROVIDER_OPEN = "EVT_PROVIDER_OPEN"
EVT_PROVIDER_TRIP = "EVT_PROVIDER_TRIP"
EVT_PROVIDER_CLOSE = "EVT_PROVIDER_CLOSE"

ClockFn = Callable[[], float]
EventCallback = Callable[[str, Optional[dict]], None]


class ProviderCircuitOpenError(RuntimeError):
    """Raised when the circuit breaker is open and rejecting calls."""


class ProviderTimeoutError(TimeoutError):
    """Raised when a provider invocation exceeds the allotted timeout."""


class ProviderCircuitBreaker:
    """Simple circuit breaker with error/timeout thresholds."""

    def __init__(
        self,
        *,
        on_event: EventCallback,
        clock: ClockFn | None = None,
        error_threshold: int = 3,
        timeout_threshold: int = 2,
        timeout_window_s: float = 30.0,
        half_open_after_s: float = 15.0,
    ) -> None:
        if error_threshold < 1:
            raise ValueError("error_threshold must be >= 1")
        if timeout_threshold < 1:
            raise ValueError("timeout_threshold must be >= 1")
        if timeout_window_s <= 0:
            raise ValueError("timeout_window_s must be > 0")
        if half_open_after_s <= 0:
            raise ValueError("half_open_after_s must be > 0")

        self._state = "closed"
        self._on_event = on_event
        self._clock = clock or time.monotonic
        self._error_threshold = error_threshold
        self._timeout_threshold = timeout_threshold
        self._timeout_window_s = timeout_window_s
        self._half_open_after_s = half_open_after_s
        self._opened_at: float | None = None
        self._consecutive_errors = 0
        self._timeouts: Deque[float] = deque()
        self._probe_active = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        """Return the current breaker state (closed/open/half_open)."""

        return self._state

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def before_call(self) -> None:
        """Raise if the breaker is open; transition to half-open when ready."""

        now = self._clock()
        if self._state == "open":
            if self._opened_at is not None and now - self._opened_at >= self._half_open_after_s:
                self._state = "half_open"
                self._probe_active = True
                self._on_event(EVT_PROVIDER_OPEN, {"state": "half_open"})
                return
            raise ProviderCircuitOpenError("provider circuit is open")

        if self._state == "half_open":
            if self._probe_active:
                raise ProviderCircuitOpenError("half-open probe already active")
            self._probe_active = True
            return

        self._probe_active = True

    def record_success(self) -> None:
        """Reset error counters and close the breaker if necessary."""

        self._probe_active = False
        self._consecutive_errors = 0
        self._timeouts.clear()
        if self._state != "closed":
            self._state = "closed"
            self._opened_at = None
            self._on_event(EVT_PROVIDER_CLOSE, {"state": "closed"})

    def record_failure(self, *, is_timeout: bool) -> bool:
        """Register a failure and return whether another attempt is allowed."""

        self._probe_active = False
        now = self._clock()
        self._consecutive_errors += 1
        if is_timeout:
            self._timeouts.append(now)
        self._trim_timeouts(now)

        if self._state == "half_open":
            self._trip(now, reason="half_open_failure")
            return False

        if self._consecutive_errors >= self._error_threshold:
            self._trip(now, reason="error_threshold")
            return False

        if len(self._timeouts) >= self._timeout_threshold:
            self._trip(now, reason="timeout_threshold")
            return False

        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _trim_timeouts(self, now: float) -> None:
        window = self._timeout_window_s
        while self._timeouts and now - self._timeouts[0] > window:
            self._timeouts.popleft()

    def _trip(self, now: float, *, reason: str) -> None:
        self._state = "open"
        self._opened_at = now
        self._on_event(EVT_PROVIDER_TRIP, {"state": "open", "reason": reason})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Force-close the breaker and clear counters."""

        self._state = "closed"
        self._opened_at = None
        self._consecutive_errors = 0
        self._timeouts.clear()
        self._probe_active = False


class ProviderAdapterBase(abc.ABC):
    """Shared plumbing for provider adapters with retry & breaker logic."""

    def __init__(
        self,
        *,
        vendor: str,
        provider_type: str,
        telemetry_bus=bus,
        retries: int = 1,
        timeout_s: float = 10.0,
        clock: ClockFn | None = None,
    ) -> None:
        if not vendor:
            raise ValueError("vendor must be provided")
        if retries < 0:
            raise ValueError("retries must be >= 0")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")

        self._vendor = vendor
        self._provider_type = provider_type
        self._bus = telemetry_bus
        self._timeout_s = float(timeout_s)
        self._max_attempts = int(retries) + 1
        self._breaker = ProviderCircuitBreaker(on_event=self._emit_breaker_event, clock=clock)

    @property
    def vendor(self) -> str:
        return self._vendor

    @property
    def breaker_state(self) -> str:
        return self._breaker.state

    async def _invoke_with_breaker(
        self,
        operation: Callable[..., Awaitable[object]],
        *args,
        **kwargs,
    ) -> object:
        attempt = 0
        last_error: BaseException | None = None
        while attempt < self._max_attempts:
            self._breaker.before_call()
            try:
                result = operation(*args, **kwargs)
                if not inspect.isawaitable(result):
                    raise TypeError("provider operations must return an awaitable")
                outcome = await asyncio.wait_for(result, timeout=self._timeout_s)
            except asyncio.TimeoutError as exc:
                last_error = ProviderTimeoutError(
                    f"{self._provider_type} provider '{self._vendor}' timed out"
                )
                retry_allowed = self._breaker.record_failure(is_timeout=True)
                attempt += 1
                if retry_allowed and attempt < self._max_attempts:
                    continue
                raise last_error from exc
            except Exception as exc:  # pragma: no cover - broad but intentional
                last_error = exc
                retry_allowed = self._breaker.record_failure(is_timeout=False)
                attempt += 1
                if retry_allowed and attempt < self._max_attempts:
                    continue
                raise
            else:
                self._breaker.record_success()
                return outcome
        if last_error is not None:
            raise last_error
        raise RuntimeError("provider invocation aborted without executing")

    def _emit_breaker_event(self, event_type: str, meta: Optional[dict]) -> None:
        event = {
            "type": event_type,
            "vendor": self._vendor,
            "provider_type": self._provider_type,
            "source": f"{self._provider_type}_breaker",
        }
        if meta:
            event["meta"] = meta
        self._bus.publish(event)


class ASRProviderBase(ProviderAdapterBase):
    """Abstract base class for streaming ASR providers."""

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
            provider_type="asr",
            telemetry_bus=telemetry_bus,
            retries=retries,
            timeout_s=timeout_s,
            clock=clock,
        )

    async def recognize(self, *args, **kwargs) -> object:
        """Invoke the provider-specific recognition implementation."""

        return await self._invoke_with_breaker(self._recognize_impl, *args, **kwargs)

    @abc.abstractmethod
    async def _recognize_impl(self, *args, **kwargs) -> object:
        """Concrete providers must implement this coroutine."""


__all__ = [
    "ASRProviderBase",
    "ProviderAdapterBase",
    "EVT_PROVIDER_CLOSE",
    "EVT_PROVIDER_OPEN",
    "EVT_PROVIDER_TRIP",
    "ProviderCircuitBreaker",
    "ProviderCircuitOpenError",
    "ProviderTimeoutError",
]
