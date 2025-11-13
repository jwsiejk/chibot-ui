"""GCP-based ASR engine implementation."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from google.cloud import speech


logger = logging.getLogger(__name__)


ResultCallback = Callable[[str, bool], Optional[Awaitable[None]]]


class ASREngine:
    """Abstract interface for ASR engines."""

    async def open(
        self,
        *,
        sample_rate: int,
        language: str,
        sid: str,
        on_result: ResultCallback,
    ) -> None:
        raise NotImplementedError

    async def write(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class GCPStreamingASREngine(ASREngine):
    """GCP Streaming Speech-to-Text engine implementation."""

    def __init__(self, client: Optional[speech.SpeechClient] = None) -> None:
        self._client = client or speech.SpeechClient()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue[Optional[bytes]]] = None
        self._response_future: Optional[asyncio.Task[None]] = None
        self._on_result: Optional[ResultCallback] = None
        self._sid: Optional[str] = None
        self._closed = False
        self._streaming_config: Optional[speech.StreamingRecognitionConfig] = None

    async def open(
        self,
        *,
        sample_rate: int,
        language: str,
        sid: str,
        on_result: ResultCallback,
    ) -> None:
        if self._queue is not None:
            raise RuntimeError("Engine already opened")

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._on_result = on_result
        self._sid = sid
        self._closed = False

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            language_code=language,
        )
        self._streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
        )

        logger.info("evt=asr_open vendor=gcp sid=%s sample_rate=%s language=%s", sid, sample_rate, language)

        self._response_future = asyncio.create_task(self._run_streaming())

    async def write(self, pcm: bytes) -> None:
        if self._queue is None or self._closed:
            raise RuntimeError("Engine not open or already closed")
        await self._queue.put(pcm)

    async def close(self) -> None:
        if self._queue is None or self._closed:
            return

        self._closed = True
        await self._queue.put(None)

        if self._response_future is not None:
            try:
                await self._response_future
            finally:
                self._response_future = None

        logger.info("evt=asr_close vendor=gcp sid=%s", self._sid)

        self._queue = None
        self._on_result = None
        self._sid = None
        self._streaming_config = None

    # Internal helpers -------------------------------------------------

    async def _run_streaming(self) -> None:
        assert self._loop is not None
        await self._loop.run_in_executor(None, self._streaming_worker)

    def _streaming_worker(self) -> None:
        assert self._loop is not None
        assert self._queue is not None
        assert self._streaming_config is not None

        def request_generator():
            while True:
                chunk = asyncio.run_coroutine_threadsafe(self._queue.get(), self._loop).result()
                if chunk is None:
                    break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        try:
            responses = self._client.streaming_recognize(
                config=self._streaming_config,
                requests=request_generator(),
            )
            for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    transcript = result.alternatives[0].transcript
                    is_final = result.is_final
                    self._loop.call_soon_threadsafe(
                        self._handle_result,
                        transcript,
                        is_final,
                    )
        except Exception:
            logger.exception("evt=asr_error vendor=gcp sid=%s", self._sid)
        finally:
            # Ensure request generator exits.
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)

    def _handle_result(self, transcript: str, is_final: bool) -> None:
        if self._on_result is None:
            return

        if is_final:
            logger.info("evt=asr_final vendor=gcp sid=%s", self._sid)
        else:
            logger.info("evt=asr_partial vendor=gcp sid=%s", self._sid)

        try:
            maybe_coro = self._on_result(transcript, is_final)
            if asyncio.iscoroutine(maybe_coro):
                asyncio.create_task(maybe_coro)
        except Exception:  # pragma: no cover - defensive
            logger.exception("evt=asr_error vendor=gcp sid=%s", self._sid)
