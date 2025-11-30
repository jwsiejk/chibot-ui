"""GCP-based ASR engine implementation."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from array import array
from typing import Awaitable, Callable, Optional

from google.api_core.exceptions import OutOfRange
from google.cloud import speech

from app import config


logger = logging.getLogger(__name__)


ResultCallback = Callable[[str, bool], Optional[Awaitable[None]]]

_RMS_TARGET_FLOOR = 0.05  # Target normalized RMS (0.0 - 1.0) before gain.
_RMS_MAX_AUTOMATIC_GAIN = 4.0  # Avoid aggressive amplification that could clip.
_RMS_EPSILON = 1e-6  # Protect against divide-by-zero when audio is silent.


@dataclass
class AsrStreamStats:
    sid: str
    created_ts: float = field(default_factory=time.monotonic)
    first_audio_ts: Optional[float] = None
    last_audio_ts: Optional[float] = None
    bytes_sent: int = 0
    partial_count: int = 0
    last_partial_text: str = ""

    def mark_audio_chunk(self, byte_count: int) -> None:
        now = time.monotonic()
        if self.first_audio_ts is None:
            self.first_audio_ts = now
        self.last_audio_ts = now
        self.bytes_sent += byte_count

    def mark_partial(self, text: str) -> None:
        self.partial_count += 1
        if text:
            self.last_partial_text = text

    def to_summary(self, outcome: str) -> dict:
        now = time.monotonic()
        return {
            "sid": self.sid,
            "outcome": outcome,
            "created_ms_ago": int((now - self.created_ts) * 1000),
            "first_audio_ms_ago": (
                int((now - self.first_audio_ts) * 1000)
                if self.first_audio_ts is not None
                else None
            ),
            "last_audio_ms_ago": (
                int((now - self.last_audio_ts) * 1000)
                if self.last_audio_ts is not None
                else None
            ),
            "bytes_sent": self.bytes_sent,
            "partial_count": self.partial_count,
            "last_partial_text": self.last_partial_text,
        }


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


def _normalized_linear16_rms(pcm: bytes) -> float:
    """Return the RMS level normalized to the LINEAR16 full-scale range."""

    if not pcm:
        return 0.0

    if len(pcm) % 2:
        raise ValueError("PCM buffer length must be a multiple of 2 bytes for LINEAR16 audio")

    samples = array("h")  # signed short (16-bit)
    samples.frombytes(pcm)

    if not samples:
        return 0.0

    square_sum = 0
    for value in samples:
        square_sum += value * value

    rms = (square_sum / len(samples)) ** 0.5
    return rms / 32768.0


def _auto_gain_for_rms(pcm: bytes) -> float:
    """Calculate an automatic gain multiplier to reach the RMS floor."""

    rms = _normalized_linear16_rms(pcm)
    if rms <= _RMS_EPSILON:
        return 1.0

    if rms >= _RMS_TARGET_FLOOR:
        return 1.0

    required_gain = _RMS_TARGET_FLOOR / rms
    return min(required_gain, _RMS_MAX_AUTOMATIC_GAIN)


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
        self._stats: Optional[AsrStreamStats] = None
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
        self._stats = AsrStreamStats(sid=sid)
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
            # Keep recognition open until we explicitly close it instead of
            # relying on GCP's end-of-utterance detection so that the first
            # user speech is not prematurely missed.
            single_utterance=False,
        )

        logger.info(
            "evt=asr_open vendor=gcp sid=%s sample_rate=%s language=%s",
            sid,
            resolved_sample_rate,
            resolved_language,
            extra={"sid": sid, "event": "asr_vendor_open"},
        )

        self._response_future = asyncio.create_task(self._run_streaming())

    async def write(self, pcm: bytes) -> None:
        if self._queue is None or self._closed:
            raise RuntimeError("Engine not open or already closed")
        gain = self._input_gain or 1.0
        if pcm:
            try:
                gain *= _auto_gain_for_rms(pcm)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "evt=asr_error vendor=gcp sid=%s",
                    self._sid,
                    extra={"sid": self._sid, "event": "asr_error"},
                )
        if gain != 1.0:
            try:
                pcm = _apply_linear16_gain(pcm, gain)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "evt=asr_error vendor=gcp sid=%s",
                    self._sid,
                    extra={"sid": self._sid, "event": "asr_error"},
                )
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
            extra={"sid": self._sid, "event": "asr_vendor_close"},
        )

        self._queue = None
        self._on_result = None
        self._sid = None
        self._sample_rate = None
        self._language = None
        self._streaming_config = None
        self._stats = None
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
                if self._stats is not None:
                    self._stats.mark_audio_chunk(len(chunk))
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        asr_stream_wait_start = time.monotonic()
        logger.info(
            "evt=asr_stream_wait_start vendor=gcp",
            extra={"sid": self._sid, "event": "asr_stream_wait_start"},
        )

        first_chunk_at = None
        try:
            responses = self._client.streaming_recognize(
                config=self._streaming_config,
                requests=request_generator(),
            )
            for response in responses:
                now = time.monotonic()
                if first_chunk_at is None:
                    first_chunk_at = now
                    wait_ms = int((now - asr_stream_wait_start) * 1000)
                    logger.info(
                        "evt=asr_first_audio_chunk vendor=gcp",
                        extra={
                            "sid": self._sid,
                            "event": "asr_first_audio_chunk",
                            "wait_ms": wait_ms,
                        },
                    )
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
            logger.info(
                "evt=asr_stream_summary vendor=gcp sid=%s outcome=final",
                self._sid,
                extra={
                    "sid": self._sid,
                    "event": "asr_stream_summary",
                    "summary": self._stats.to_summary("final") if self._stats else None,
                },
            )
        except Exception as exc:
            # Treat GCP "Audio Timeout Error" as a soft end-of-turn.
            if isinstance(exc, OutOfRange) and "Audio Timeout Error" in str(exc):
                logger.warning(
                    "evt=asr_timeout vendor=gcp sid=%s",
                    self._sid,
                    exc_info=True,
                    extra={"sid": self._sid, "event": "asr_timeout"},
                )
                logger.info(
                    "evt=asr_stream_summary vendor=gcp sid=%s outcome=timeout",
                    self._sid,
                    extra={
                        "sid": self._sid,
                        "event": "asr_stream_summary",
                        "summary": self._stats.to_summary("timeout") if self._stats else None,
                    },
                )
                if self._last_transcript is not None and not self._last_is_final:
                    self._loop.call_soon_threadsafe(
                        self._handle_result,
                        self._last_transcript,
                        True,
                    )
            else:
                logger.exception(
                    "evt=asr_error vendor=gcp sid=%s",
                    self._sid,
                    extra={"sid": self._sid, "event": "asr_error"},
                )
                logger.info(
                    "evt=asr_stream_summary vendor=gcp sid=%s outcome=error",
                    self._sid,
                    extra={
                        "sid": self._sid,
                        "event": "asr_stream_summary",
                        "summary": self._stats.to_summary("error") if self._stats else None,
                    },
                )
        finally:
            if first_chunk_at is None:
                idle_ms = int((time.monotonic() - asr_stream_wait_start) * 1000)
                logger.warning(
                    "evt=asr_no_audio_before_timeout vendor=gcp",
                    extra={
                        "sid": self._sid,
                        "event": "asr_no_audio_before_timeout",
                        "idle_ms": idle_ms,
                    },
                )
            # Ensure request generator exits.
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)

    def _handle_result(self, transcript: str, is_final: bool) -> None:
        if self._on_result is None:
            return

        self._last_transcript = transcript
        self._last_is_final = is_final

        if is_final:
            logger.info(
                "evt=asr_final vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_final"},
            )
        else:
            logger.info(
                "evt=asr_partial vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_partial"},
            )

        if self._stats is not None:
            self._stats.mark_partial(transcript)

        try:
            maybe_coro = self._on_result(transcript, is_final)
            if asyncio.iscoroutine(maybe_coro):
                asyncio.create_task(maybe_coro)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "evt=asr_error vendor=gcp sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_error"},
            )
