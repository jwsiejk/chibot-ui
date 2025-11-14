"""GCP-based ASR engine implementation."""
from __future__ import annotations

import asyncio
import logging
from array import array
from typing import Awaitable, Callable, Optional

from google.api_core.exceptions import OutOfRange
from google.cloud import speech

from app import config


logger = logging.getLogger(__name__)


ResultCallback = Callable[[str, bool], Optional[Awaitable[None]]]


def _apply_linear16_gain(pcm: bytes, gain: float) -> bytes:
    """Apply a gain factor to 16-bit linear PCM audio."""

    if not pcm:
        return pcm

    if len(pcm) % 2:
        raise ValueError("PCM buffer length must be a multiple of 2 bytes for LINEAR16 audio")

    samples = array("h")  # signed short (16-bit)
    samples.frombytes(pcm)

    max_sample = 32767
    min_sample = -32768

    for index, sample in enumerate(samples):
        amplified = int(round(sample * gain))
        if amplified > max_sample:
            amplified = max_sample
        elif amplified < min_sample:
            amplified = min_sample
        samples[index] = amplified

    return samples.tobytes()


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
        self._sample_rate: Optional[int] = None
        self._language: Optional[str] = None
        self._input_gain = config.GCP_STT_INPUT_GAIN
        self._last_transcript: Optional[str] = None
        self._last_is_final: bool = False

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
        self._last_transcript = None
        self._last_is_final = False

        resolved_sample_rate = (
            sample_rate if sample_rate and sample_rate > 0 else config.GCP_STT_DEFAULT_SAMPLE_RATE
        )
        if resolved_sample_rate <= 0:
            resolved_sample_rate = config.GCP_STT_DEFAULT_SAMPLE_RATE
        resolved_language = (language or "").strip() or config.GCP_STT_DEFAULT_LANGUAGE

        self._sample_rate = resolved_sample_rate
        self._language = resolved_language

        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=resolved_sample_rate,
            language_code=resolved_language,
        )
        self._streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
        )

        logger.info(
            "evt=asr_open vendor=gcp sid=%s sample_rate=%s language=%s",
            sid,
            resolved_sample_rate,
            resolved_language,
        )

        self._response_future = asyncio.create_task(self._run_streaming())

    async def write(self, pcm: bytes) -> None:
        if self._queue is None or self._closed:
            raise RuntimeError("Engine not open or already closed")
        if self._input_gain and self._input_gain != 1.0:
            try:
                pcm = _apply_linear16_gain(pcm, self._input_gain)
            except Exception:  # pragma: no cover - defensive
                logger.exception("evt=asr_error vendor=gcp sid=%s", self._sid)
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

        logger.info(
            "evt=asr_close vendor=gcp sid=%s sample_rate=%s language=%s",
            self._sid,
            self._sample_rate,
            self._language,
        )

        self._queue = None
        self._on_result = None
        self._sid = None
        self._sample_rate = None
        self._language = None
        self._streaming_config = None
        self._last_transcript = None
        self._last_is_final = False

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
        except Exception as exc:
            # Treat GCP "Audio Timeout Error" as a soft end-of-turn.
            if isinstance(exc, OutOfRange) and "Audio Timeout Error" in str(exc):
                logger.warning(
                    "evt=asr_timeout vendor=gcp sid=%s", self._sid, exc_info=True
                )
                if self._last_transcript is not None and not self._last_is_final:
                    self._loop.call_soon_threadsafe(
                        self._handle_result,
                        self._last_transcript,
                        True,
                    )
            else:
                logger.exception("evt=asr_error vendor=gcp sid=%s", self._sid)
        finally:
            # Ensure request generator exits.
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)

    def _handle_result(self, transcript: str, is_final: bool) -> None:
        if self._on_result is None:
            return

        self._last_transcript = transcript
        self._last_is_final = is_final

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
