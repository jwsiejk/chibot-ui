"""ElevenLabs TTS provider that streams PCM16 audio for Engine v2."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

import httpx

from app.telemetry import bus
from app.voice_v2.tts_base import TTSProviderBase

_log = logging.getLogger(__name__)

_API_BASE_URL = "https://api.elevenlabs.io/v1"
_OUTPUT_FORMAT = "pcm_16000"
_SAMPLE_RATE_HZ = 16000
_CHANNELS = 1
_SAMPLE_WIDTH_BYTES = 2  # 16-bit
# Emit roughly 40 ms of audio per chunk (16 kHz mono => 1280 bytes per slice).
_CHUNK_BYTES = int(_SAMPLE_RATE_HZ * _CHANNELS * _SAMPLE_WIDTH_BYTES * 0.04)


@dataclass
class ElevenLabsStream:
    """Asynchronous iterator that yields PCM chunks from ElevenLabs."""

    response_cm: Any
    chunk_bytes: int = _CHUNK_BYTES

    def __post_init__(self) -> None:
        self._response: httpx.Response | None = None
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._producer: asyncio.Task[None] | None = None
        self._sentinel = object()
        self._error: BaseException | None = None
        self._closed = False

    def __await__(self):  # type: ignore[override]
        return self._start().__await__()

    async def _start(self) -> "ElevenLabsStream":
        response = await self.response_cm.__aenter__()
        self._response = response
        request = getattr(response, "request", None)
        query_string: str | None = None
        if request is not None:
            raw_query = request.url.query
            if isinstance(raw_query, bytes):
                query_string = raw_query.decode()
            elif raw_query:
                query_string = str(raw_query)
        if query_string is None:
            query_string = f"optimize_streaming_latency=4&output_format={_OUTPUT_FORMAT}"
        _log.info(
            "evt=tts_provider_stream_opened provider=elevenlabs content_type=%s url_qs=%s",
            response.headers.get("content-type"),
            query_string,
        )
        response.raise_for_status()
        self._producer = asyncio.create_task(self._drain())
        return self

    async def _drain(self) -> None:
        assert self._response is not None
        buffer = bytearray()
        try:
            async for data in self._response.aiter_bytes():
                if not data:
                    continue
                buffer.extend(data)
                while len(buffer) >= self.chunk_bytes:
                    await self._queue.put(bytes(buffer[: self.chunk_bytes]))
                    del buffer[: self.chunk_bytes]
            if buffer:
                await self._queue.put(bytes(buffer))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._error = exc
        finally:
            await self._queue.put(self._sentinel)
            await self._close_response()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            if item is self._sentinel:
                break
            yield bytes(item)
        if self._error is not None:
            raise self._error

    async def aclose(self) -> None:
        if self._closed:
            return
        producer = self._producer
        if producer is not None and not producer.done():
            producer.cancel()
        if producer is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await producer
        await self._close_response()

    async def cancel(self) -> None:
        await self.aclose()

    async def _close_response(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._response is not None:
            try:
                await self.response_cm.__aexit__(None, None, None)
            finally:
                self._response = None


class ElevenLabsTTSProvider(TTSProviderBase):
    """TTS provider that requests streaming PCM 16 kHz audio from ElevenLabs."""

    def __init__(
        self,
        *,
        telemetry_bus=bus,
        retries: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY must be set for ElevenLabsTTSProvider")
        default_voice = os.getenv("ELEVENLABS_VOICE_ID") or None
        retry_count = retries if retries is not None else int(os.getenv("TTS_RETRIES", "1") or 1)
        timeout = timeout_s if timeout_s is not None else float(os.getenv("TTS_TIMEOUT_S", "8") or 8)

        super().__init__(
            vendor="elevenlabs",
            telemetry_bus=telemetry_bus,
            retries=max(0, retry_count),
            timeout_s=max(float(timeout), 0.1),
        )
        self._api_key = api_key
        self._default_voice_id = default_voice
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._timeout = httpx.Timeout(None, connect=5.0, write=5.0, read=None)

    @property
    def default_voice_id(self) -> str | None:
        return self._default_voice_id

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def _synthesize_impl(self, text: str, *, voice_id: Optional[str] = None, **kwargs) -> ElevenLabsStream:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text:
            raise ValueError("text must not be empty")
        target_voice = voice_id or self._default_voice_id
        if not target_voice:
            raise RuntimeError("voice_id must be provided for ElevenLabs synthesis")

        client = await self._get_client()
        payload: Dict[str, object] = {"text": text, "output_format": _OUTPUT_FORMAT}
        model_id = kwargs.get("model_id") or os.getenv("ELEVENLABS_MODEL_ID")
        if isinstance(model_id, str) and model_id:
            payload["model_id"] = model_id

        headers = {"xi-api-key": self._api_key, "accept": "application/octet-stream"}
        response_cm = client.stream(
            "POST",
            f"/text-to-speech/{target_voice}/stream",
            json=payload,
            headers=headers,
            params={
                "optimize_streaming_latency": "4",
                "output_format": _OUTPUT_FORMAT,
            },
        )
        stream = ElevenLabsStream(response_cm=response_cm)
        return await stream

    async def _get_client(self) -> httpx.AsyncClient:
        client = self._client
        if client is not None:
            return client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(base_url=_API_BASE_URL, timeout=self._timeout)
            return self._client


__all__ = ["ElevenLabsTTSProvider", "ElevenLabsStream"]
