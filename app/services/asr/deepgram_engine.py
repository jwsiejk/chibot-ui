"""Deepgram-based ASR engine implementation."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import urlencode

from app import config
from app.services.asr.base_engine import ASREngine, ResultCallback

logger = logging.getLogger(__name__)

_DEFAULT_DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


@dataclass
class DeepgramStreamStats:
    sid: str
    created_ts: float = field(default_factory=time.monotonic)
    bytes_sent: int = 0
    partial_count: int = 0

    def mark_audio_chunk(self, byte_count: int) -> None:
        self.bytes_sent += byte_count

    def mark_partial(self) -> None:
        self.partial_count += 1


class DeepgramStreamingASREngine(ASREngine):
    """Deepgram Streaming Speech-to-Text engine implementation."""

    def __init__(
        self,
        *,
        websocket_factory: Optional[
            Callable[[str, Mapping[str, str]], Awaitable[Any]]
        ] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        self._websocket_factory = websocket_factory or self._default_websocket_factory
        self._api_key = api_key or config.DEEPGRAM_API_KEY
        self._endpoint = endpoint or _DEFAULT_DEEPGRAM_URL
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue[Optional[bytes]]] = None
        self._ws: Any = None
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._send_task: Optional[asyncio.Task[None]] = None
        self._on_result: Optional[ResultCallback] = None
        self._sid: Optional[str] = None
        self._stats: Optional[DeepgramStreamStats] = None
        self._closed = False
        self._final_emitted = False
        self._sample_rate: Optional[int] = None
        self._language: Optional[str] = None

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

        if not self._api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is required to use Deepgram STT")

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._on_result = on_result
        self._sid = sid
        self._stats = DeepgramStreamStats(sid=sid)
        self._closed = False
        self._final_emitted = False

        resolved_sample_rate = (
            sample_rate
            if sample_rate and sample_rate > 0
            else config.DEEPGRAM_STT_SAMPLE_RATE
        )
        if resolved_sample_rate <= 0:
            resolved_sample_rate = config.DEEPGRAM_STT_SAMPLE_RATE
        resolved_language = (language or "").strip() or config.DEEPGRAM_STT_LANGUAGE

        self._sample_rate = resolved_sample_rate
        self._language = resolved_language

        url = self._build_url(resolved_sample_rate, resolved_language)
        headers = {"Authorization": f"Token {self._api_key}"}

        logger.info(
            "evt=asr_open vendor=deepgram sid=%s sample_rate=%s language=%s",
            sid,
            resolved_sample_rate,
            resolved_language,
            extra={"sid": sid, "event": "asr_vendor_open"},
        )

        self._ws = await self._websocket_factory(url, headers)
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._send_task = asyncio.create_task(self._send_loop())

    async def write(self, pcm: bytes) -> None:
        if self._queue is None or self._closed:
            raise RuntimeError("Engine not open or already closed")
        await self._queue.put(pcm)

    async def close(self) -> None:
        if self._queue is None or self._closed:
            return

        self._closed = True
        await self._queue.put(None)

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.exception(
                    "evt=asr_error vendor=deepgram sid=%s",
                    self._sid,
                    extra={"sid": self._sid, "event": "asr_error"},
                )

        if self._send_task is not None:
            await self._await_task(self._send_task)
            self._send_task = None

        if self._recv_task is not None:
            await self._await_task(self._recv_task)
            self._recv_task = None

        logger.info(
            "evt=asr_close vendor=deepgram sid=%s reason=client_close",
            self._sid,
            extra={"sid": self._sid, "event": "asr_vendor_close"},
        )

        self._queue = None
        self._on_result = None
        self._sid = None
        self._stats = None
        self._ws = None
        self._sample_rate = None
        self._language = None

    async def _await_task(self, task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "evt=asr_error vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_error"},
            )

    async def _default_websocket_factory(
        self, url: str, headers: Mapping[str, str]
    ) -> Any:
        try:
            import websockets  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("websockets dependency is required for Deepgram") from exc
        return await websockets.connect(
            url,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )

    def _build_url(self, sample_rate: int, language: str) -> str:
        params: dict[str, str] = {
            "encoding": "linear16",
            "sample_rate": str(sample_rate),
            "channels": "1",
            "interim_results": "true"
            if config.DEEPGRAM_STT_INTERIM_RESULTS
            else "false",
        }
        model = (config.DEEPGRAM_STT_MODEL or "").strip()
        if model:
            params["model"] = model
        if language:
            params["language"] = language
        if config.DEEPGRAM_STT_ENDPOINTING_MS:
            params["endpointing"] = str(config.DEEPGRAM_STT_ENDPOINTING_MS)

        return f"{self._endpoint}?{urlencode(params)}"

    async def _send_loop(self) -> None:
        assert self._queue is not None
        assert self._ws is not None
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            if self._stats is not None:
                self._stats.mark_audio_chunk(len(chunk))
            try:
                await self._ws.send(chunk)
            except Exception:
                logger.exception(
                    "evt=asr_error vendor=deepgram sid=%s",
                    self._sid,
                    extra={"sid": self._sid, "event": "asr_error"},
                )
                break

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning(
                        "evt=asr_error vendor=deepgram sid=%s reason=invalid_json",
                        self._sid,
                        extra={"sid": self._sid, "event": "asr_error"},
                    )
                    continue
                self._handle_message(payload)
        except Exception:
            logger.exception(
                "evt=asr_error vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_error"},
            )

    def _handle_message(self, payload: Mapping[str, Any]) -> None:
        if self._on_result is None:
            return
        parsed = self._parse_result_message(payload)
        if parsed is None:
            return
        transcript, is_final, event = parsed

        if is_final and self._final_emitted:
            logger.info(
                "evt=asr_final_duplicate vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_final_duplicate"},
            )
            return

        if is_final:
            self._final_emitted = True
            logger.info(
                "evt=asr_final vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_final"},
            )
        else:
            logger.info(
                "evt=asr_partial vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_partial"},
            )

        if self._stats is not None:
            self._stats.mark_partial()

        try:
            maybe_coro = self._on_result(transcript, is_final, event)
            if asyncio.iscoroutine(maybe_coro):
                asyncio.create_task(maybe_coro)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "evt=asr_error vendor=deepgram sid=%s",
                self._sid,
                extra={"sid": self._sid, "event": "asr_error"},
            )

    def _parse_result_message(
        self, payload: Mapping[str, Any]
    ) -> Optional[tuple[str, bool, Optional[Mapping[str, Any]]]]:
        if not isinstance(payload, Mapping):
            return None
        payload_type = payload.get("type")
        if isinstance(payload_type, str) and payload_type.lower() == "error":
            logger.error(
                "evt=asr_error vendor=deepgram sid=%s error=%s",
                self._sid,
                payload,
                extra={"sid": self._sid, "event": "asr_error"},
            )
            return None

        channel = payload.get("channel")
        if not isinstance(channel, Mapping):
            return None
        alternatives = channel.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            return None
        alternative = alternatives[0]
        if not isinstance(alternative, Mapping):
            return None

        transcript = str(alternative.get("transcript") or "")
        is_final = bool(payload.get("is_final")) or bool(payload.get("speech_final"))
        confidence = alternative.get("confidence")
        event: Optional[Mapping[str, Any]] = None
        if confidence is not None:
            event = {"confidence": confidence}
        return transcript, is_final, event
