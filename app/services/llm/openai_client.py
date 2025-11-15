"""OpenAI LLM provider implementation with circuit breaker integration."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import asyncio
import logging
import queue
import threading

from openai import OpenAI

from app.config import get_env
from app.telemetry import bus
from app.voice_v2.llm_base import LLMProviderBase

_log = logging.getLogger(__name__)


class _TokenSentinel:
    """Sentinel payload used to mark stream completion or failure."""

    __slots__ = ("error",)

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


class ThreadedTokenStream:
    """Thread-backed stream that fan-outs LLM tokens to subscribers."""

    def __init__(self, *, producer: callable) -> None:
        self._producer = producer
        self._lock = threading.Lock()
        self._subscribers: list[dict[str, Any]] = []
        self._tokens: list[str] = []
        self._final_text: Optional[str] = None
        self._error: BaseException | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="openai-token-stream", daemon=True
        )
        self._thread.start()

    def _register_subscriber(self, subscriber: dict[str, Any]) -> None:
        with self._lock:
            for token in self._tokens:
                self._dispatch_token(subscriber, token)
            if self._done.is_set():
                self._dispatch_sentinel(subscriber, self._error)
            else:
                self._subscribers.append(subscriber)

    def _unregister_subscriber(self, subscriber: dict[str, Any]) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _dispatch_token(self, subscriber: dict[str, Any], token: str) -> None:
        if subscriber["type"] == "sync":
            subscriber["queue"].put(token)
        else:
            loop = subscriber["loop"]
            loop.call_soon_threadsafe(subscriber["queue"].put_nowait, token)

    def _dispatch_sentinel(
        self, subscriber: dict[str, Any], error: BaseException | None
    ) -> None:
        payload = _TokenSentinel(error)
        if subscriber["type"] == "sync":
            subscriber["queue"].put(payload)
        else:
            loop = subscriber["loop"]
            loop.call_soon_threadsafe(subscriber["queue"].put_nowait, payload)

    def subscribe(self) -> Iterable[str]:
        stream_queue: "queue.Queue[str | _TokenSentinel]" = queue.Queue()
        subscriber = {"type": "sync", "queue": stream_queue}
        self._register_subscriber(subscriber)

        try:
            while True:
                item = stream_queue.get()
                if isinstance(item, _TokenSentinel):
                    if item.error is not None:
                        raise item.error
                    break
                yield item
        finally:
            self._unregister_subscriber(subscriber)

    async def subscribe_async(self):
        loop = asyncio.get_running_loop()
        stream_queue: "asyncio.Queue[str | _TokenSentinel]" = asyncio.Queue()
        subscriber = {"type": "async", "queue": stream_queue, "loop": loop}
        self._register_subscriber(subscriber)

        try:
            while True:
                item = await stream_queue.get()
                if isinstance(item, _TokenSentinel):
                    if item.error is not None:
                        raise item.error
                    break
                yield item
        finally:
            self._unregister_subscriber(subscriber)

    def __iter__(self) -> Iterable[str]:
        return self.subscribe()

    def __aiter__(self):
        return self.subscribe_async()

    def final_text(self, timeout: Optional[float] = None) -> str:
        self._done.wait(timeout)
        if not self._done.is_set():
            raise TimeoutError("LLM stream did not complete within the timeout")
        if self._error is not None:
            raise self._error
        if self._thread.is_alive():
            self._thread.join()
        assert self._final_text is not None
        return self._final_text

    def _append_token(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.append(token)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            self._dispatch_token(subscriber, token)

    def _finalize(self, error: BaseException | None = None) -> None:
        if self._done.is_set():
            return
        if error is not None:
            self._error = error
        with self._lock:
            if self._final_text is None:
                combined = "".join(self._tokens).strip()
                self._final_text = combined
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for subscriber in subscribers:
            self._dispatch_sentinel(subscriber, self._error)
        self._done.set()

    def _run(self) -> None:
        try:
            for token in self._producer():
                if isinstance(token, str) and token:
                    self._append_token(token)
        except BaseException as exc:  # pragma: no cover - defensive
            _log.exception("evt=openai_stream_failed")
            self._finalize(error=exc)
        else:
            self._finalize()


def _coerce_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


class OpenAILLMProvider(LLMProviderBase):
    """Production OpenAI client wired into the shared provider base."""

    def __init__(
        self,
        *,
        telemetry_bus=bus,
        clock=None,
    ) -> None:
        api_key = get_env("OPENAI_API_KEY")
        base_url = get_env("OPENAI_BASE_URL") or None
        model = get_env("LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
        timeout_s = _coerce_float(get_env("LLM_TIMEOUT_S"), 12.0)
        retries = _coerce_int(get_env("LLM_RETRIES"), 1)

        super().__init__(
            vendor="openai",
            telemetry_bus=telemetry_bus,
            retries=retries,
            timeout_s=timeout_s,
            clock=clock,
        )

        self._api_key = api_key
        self._base_url = base_url
        self._default_model = model
        self._client = (
            OpenAI(api_key=api_key, base_url=base_url or None) if api_key else None
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    @property
    def default_model(self) -> str:
        return self._default_model

    async def _generate_impl(
        self, messages: List[Dict[str, Any]], **kwargs: Any
    ) -> ThreadedTokenStream:
        if not self.is_configured:
            raise RuntimeError("OpenAI provider not configured")

        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")

        prepared: List[Dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise TypeError("each message must be a dict")
            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                raise TypeError("message role/content must be strings")
            prepared.append({"role": role, "content": content})

        client = self._client
        assert client is not None  # for type-checkers

        model = kwargs.get("model") or self._default_model
        temperature = kwargs.get("temperature")
        max_tokens = kwargs.get("max_tokens")
        kwargs.pop("purpose", None)

        request_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": prepared,
        }
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens

        def _stream_tokens() -> Iterable[str]:
            try:
                stream = client.chat.completions.create(stream=True, **request_kwargs)
            except Exception as exc:  # pragma: no cover - defensive
                raise RuntimeError("OpenAI chat request failed") from exc

            for chunk in stream:
                if chunk is None:
                    continue
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                for choice in choices:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if isinstance(content, str):
                        yield content
                    elif isinstance(content, Iterable):
                        for part in content:
                            if isinstance(part, str):
                                yield part
                            elif isinstance(part, dict):
                                if part.get("type") == "text":
                                    part_text = part.get("text")
                                    if isinstance(part_text, str):
                                        yield part_text

        return ThreadedTokenStream(producer=_stream_tokens)


__all__ = ["OpenAILLMProvider", "ThreadedTokenStream"]
