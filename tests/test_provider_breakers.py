import asyncio
import pytest

from app.voice_v2.asr_base import (
    ASRProviderBase,
    EVT_PROVIDER_CLOSE,
    EVT_PROVIDER_OPEN,
    EVT_PROVIDER_TRIP,
    ProviderCircuitOpenError,
    ProviderTimeoutError,
)


class ManualClock:
    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)


class ScriptedASRProvider(ASRProviderBase):
    def __init__(self, script: list[str], *, bus: RecordingBus, clock: ManualClock) -> None:
        super().__init__(
            vendor="dummy",
            telemetry_bus=bus,
            retries=0,
            timeout_s=0.01,
            clock=clock,
        )
        self._script = list(script)

    async def _recognize_impl(self, *_args, **_kwargs) -> str:
        action = self._script.pop(0) if self._script else "success"
        if action == "error":
            raise RuntimeError("synthetic failure")
        if action == "timeout":
            await asyncio.sleep(self._timeout_s * 2)
            return "timeout"
        if action == "success":
            return "ok"
        raise AssertionError(f"unknown scripted action: {action}")


def _run(coro):
    return asyncio.run(coro)


def test_breaker_trips_after_three_errors_and_recovers() -> None:
    clock = ManualClock()
    bus = RecordingBus()
    provider = ScriptedASRProvider(["error", "error", "error", "success"], bus=bus, clock=clock)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            _run(provider.recognize(b"payload"))

    assert provider.breaker_state == "open"
    assert any(event["type"] == EVT_PROVIDER_TRIP for event in bus.events)

    with pytest.raises(ProviderCircuitOpenError):
        _run(provider.recognize(b"still_blocked"))

    clock.advance(16.0)
    result = _run(provider.recognize(b"probe"))
    assert result == "ok"
    assert provider.breaker_state == "closed"
    assert [event["type"] for event in bus.events[-2:]] == [EVT_PROVIDER_OPEN, EVT_PROVIDER_CLOSE]


def test_breaker_trips_after_two_timeouts() -> None:
    clock = ManualClock()
    bus = RecordingBus()
    provider = ScriptedASRProvider(["timeout", "timeout", "success"], bus=bus, clock=clock)

    with pytest.raises(ProviderTimeoutError):
        _run(provider.recognize(b"timeout-1"))

    clock.advance(1.0)
    with pytest.raises(ProviderTimeoutError):
        _run(provider.recognize(b"timeout-2"))

    assert provider.breaker_state == "open"
    assert bus.events[-1]["type"] == EVT_PROVIDER_TRIP

    clock.advance(16.0)
    result = _run(provider.recognize(b"probe-success"))
    assert result == "ok"
    assert provider.breaker_state == "closed"
    assert [event["type"] for event in bus.events[-2:]] == [EVT_PROVIDER_OPEN, EVT_PROVIDER_CLOSE]
