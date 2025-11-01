"""Speechmatics realtime streaming client."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

import websockets
from websockets.legacy.client import WebSocketClientProtocol


_AUDIO_SENTINEL = object()


def _coerce_language(policy: Mapping[str, Any] | None) -> str:
    """Return the language preference from policy or default to English."""

    if not isinstance(policy, Mapping):
        return "en"

    current: Any = policy
    for path in (
        ("vendor", "asr", "speechmatics", "language"),
        ("input", "language"),
        ("language",),
    ):
        lookup = current
        for key in path:
            if not isinstance(lookup, Mapping):
                lookup = None
                break
            lookup = lookup.get(key)
        if isinstance(lookup, str) and lookup.strip():
            return lookup.strip()
    return "en"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _latency_ms(state: "_StreamState", now_monotonic: float) -> int:
    if state.first_audio_ts is not None:
        baseline = state.first_audio_ts
    else:
        baseline = state.opened_monotonic
    latency = int(max(0.0, (now_monotonic - baseline) * 1000))
    return latency


def _extract_text(payload: Mapping[str, Any]) -> str | None:
    """Best-effort extraction of transcript text from a vendor payload."""

    if not isinstance(payload, Mapping):
        return None

    # Speechmatics real-time events typically use "results" -> alternatives.
    results = payload.get("results")
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, Mapping):
                continue
            alternatives = result.get("alternatives")
            if isinstance(alternatives, list):
                for alt in alternatives:
                    if isinstance(alt, Mapping):
                        text = alt.get("transcript") or alt.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
            transcript = result.get("transcript")
            if isinstance(transcript, str) and transcript.strip():
                return transcript

    # Some messages may embed text directly.
    for key in ("transcript", "text", "partial", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return None


def _invoke_callback(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        callback(*args, **kwargs)
    except TypeError:
        # Fallback to positional invocation with reduced arguments for legacy handlers.
        if kwargs:
            callback(*args)
        else:
            raise


def _resolve_headers_arg() -> str:
    try:
        params = inspect.signature(websockets.connect).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return "extra_headers"
    if "additional_headers" in params:
        return "additional_headers"
    return "extra_headers"


_CONNECT_HEADERS_ARG = _resolve_headers_arg()


@dataclass
class _StreamState:
    sid: str
    stream_id: str
    websocket: WebSocketClientProtocol
    on_partial: Callable[[str, Dict[str, object]], None]
    on_final: Callable[[str, Dict[str, object]], None]
    on_error: Callable[..., None]
    on_close: Optional[Callable[[int | None, str | None], None]]
    language: str
    encoding: str
    sample_rate: int
    opened_monotonic: float = field(default_factory=time.monotonic)
    opened_wall: float = field(default_factory=time.time)
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_event_loop)
    audio_queue: "asyncio.Queue[bytes | object]" = field(default_factory=asyncio.Queue)
    sender_task: Optional[asyncio.Task[None]] = None
    receiver_task: Optional[asyncio.Task[None]] = None
    closing: bool = False
    closed: bool = False
    partial_count: int = 0
    final_count: int = 0
    bytes_sent: int = 0
    first_audio_ts: float | None = None
    last_audio_ts: float | None = None
    last_partial_log: float = 0.0
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    ready_error: Optional[str] = None
    ready_error_detail: Optional[str] = None


class SpeechmaticsClient:
    """Client wrapper responsible for Speechmatics realtime streams."""

    def __init__(self, api_key: str, url: str, bus: Any, logger: logging.Logger) -> None:
        self._api_key = api_key
        self._url = url
        self._bus = bus
        self._logger = logger or logging.getLogger(__name__)
        self._streams: Dict[str, _StreamState] = {}

    async def open_stream(
        self,
        sid: str,
        on_partial: Callable[[str, Dict[str, object]], None],
        on_final: Callable[[str, Dict[str, object]], None],
        on_error: Callable[..., None],
        *,
        stream_id: str,
        on_close: Optional[Callable[[int | None, str | None], None]] = None,
        encoding: str = "linear16",
        sample_rate: int = 16000,
        policy: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(sid, str) or not sid:
            raise ValueError("sid must be a non-empty string")
        if not isinstance(stream_id, str) or not stream_id:
            raise ValueError("stream_id must be a non-empty string")
        if sid in self._streams:
            raise RuntimeError(f"stream for sid {sid!r} already exists")

        language = _coerce_language(policy)

        headers = {"Authorization": f"Bearer {self._api_key}"}
        connect_kwargs = {
            _CONNECT_HEADERS_ARG: headers,
            "max_size": None,
            "ping_interval": 20,
            "ping_timeout": 10,
        }
        try:
            websocket = await websockets.connect(self._url, **connect_kwargs)
        except Exception as exc:
            self._logger.exception("evt=sm_connect_failed sid=%s err=%s", sid, exc)
            raise

        vendor_stream_id = f"sm-stream-{uuid.uuid4().hex}"

        start_payload = {
            "message": "StartRecognition",
            "transcription_config": {
                "language": language,
                "enable_partials": True,
            },
            "audio_format": {
                "type": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate
            },
        }
        await websocket.send(json.dumps(start_payload))

        self._logger.info(
            "sm_ws_open encoding=%s sr=%d ch=1 interim=true lang=%s",
            encoding,
            sample_rate,
            language,
        )

        state = _StreamState(
            sid=sid,
            stream_id=vendor_stream_id,
            websocket=websocket,
            on_partial=on_partial,
            on_final=on_final,
            on_error=on_error,
            on_close=on_close,
            language=language,
            encoding=encoding,
            sample_rate=sample_rate,
            loop=asyncio.get_running_loop(),
        )
        state.sender_task = state.loop.create_task(self._sender_loop(state), name=f"sm-send-{sid}")
        state.receiver_task = state.loop.create_task(self._receiver_loop(state), name=f"sm-recv-{sid}")
        self._streams[sid] = state
        try:
            await asyncio.wait_for(state.ready_event.wait(), timeout=5.0)
        except asyncio.TimeoutError as exc:
            self._logger.error(
                "evt=sm_ready_timeout sid=%s stream_id=%s", sid, vendor_stream_id
            )
            self._handle_error(state, "ready_timeout", "no ready signal")
            raise RuntimeError("speechmatics_ready_timeout") from exc
        if state.ready_error:
            detail = (state.ready_error_detail or "").strip()
            if detail:
                message = f"speechmatics_ready_failed: {state.ready_error} {detail}"
            else:
                message = f"speechmatics_ready_failed: {state.ready_error}"
            raise RuntimeError(message)
        return vendor_stream_id

    def send_audio(self, sid: str, chunk: bytes) -> None:
        state = self._streams.get(sid)
        if state is None or state.closing:
            return
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        data = bytes(chunk)
        if not data:
            return
        now = time.monotonic()
        if state.first_audio_ts is None:
            state.first_audio_ts = now
        state.last_audio_ts = now
        state.bytes_sent += len(data)
        try:
            state.audio_queue.put_nowait(data)
        except asyncio.QueueFull:
            # Best-effort drop on overload; log once per session.
            self._logger.warning(
                "evt=sm_audio_queue_full sid=%s stream_id=%s dropped_bytes=%d",
                sid,
                state.stream_id,
                len(data),
            )

    def close_stream(self, sid: str) -> None:
        state = self._streams.pop(sid, None)
        if state is None:
            return
        if state.closing:
            return
        state.closing = True
        try:
            state.audio_queue.put_nowait(_AUDIO_SENTINEL)
        except Exception:
            pass
        state.loop.create_task(self._shutdown(state))

    async def _sender_loop(self, state: _StreamState) -> None:
        websocket = state.websocket
        try:
            try:
                await asyncio.wait_for(state.ready_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._logger.error(
                    "evt=sm_ready_timeout sid=%s stream_id=%s", state.sid, state.stream_id
                )
                self._handle_error(state, "ready_timeout", "no ready signal")
                return
            if state.ready_error:
                return
            while True:
                item = await state.audio_queue.get()
                if item is _AUDIO_SENTINEL:
                    break
                data = bytes(item)
                if not data:
                    continue
                await websocket.send(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.exception(
                "evt=sm_send_failed sid=%s stream_id=%s err=%s",
                state.sid,
                state.stream_id,
                exc,
            )
            self._handle_error(state, "send_failed", str(exc))
        finally:
            await self._finalize_sender(state)

    async def _receiver_loop(self, state: _StreamState) -> None:
        websocket = state.websocket
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    continue
                try:
                    payload = json.loads(message)
                except Exception:
                    self._logger.debug(
                        "evt=sm_msg_parse_failed sid=%s stream_id=%s", state.sid, state.stream_id
                    )
                    continue
                self._handle_payload(state, payload)
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosedOK as exc:
            await self._finalize_close(state, exc.code, exc.reason)
        except websockets.ConnectionClosedError as exc:
            self._handle_error(state, str(exc.code), exc.reason or "connection_closed")
            await self._finalize_close(state, exc.code, exc.reason)
        except Exception as exc:
            self._logger.exception(
                "evt=sm_recv_failed sid=%s stream_id=%s err=%s",
                state.sid,
                state.stream_id,
                exc,
            )
            self._handle_error(state, "recv_failed", str(exc))
            await self._finalize_close(state, None, None)
        finally:
            await self._finalize_receiver(state)

    def _mark_stream_ready(self, state: _StreamState, payload: Mapping[str, Any]) -> None:
        if state.ready_event.is_set():
            return

        message_type_raw = None
        if isinstance(payload, Mapping):
            message_type_raw = payload.get("type") or payload.get("message")
        message_type = str(message_type_raw).lower() if message_type_raw else ""

        if message_type in {"error", "error_message"}:
            code = payload.get("code") or payload.get("error_code") or "error"
            reason = payload.get("reason") or payload.get("message") or ""
            state.ready_error = str(code)
            state.ready_error_detail = str(reason)
            state.ready_event.set()
            return

        if message_type and message_type not in {
            "partial",
            "partial_transcript",
            "addpartialtranscript",
            "final",
            "transcript",
            "addtranscript",
            "endoftranscript",
        }:
            state.ready_event.set()
            return

        # Fallback: treat informational payloads with a message or status as ready.
        message_value = payload.get("message") if isinstance(payload, Mapping) else None
        status_value = payload.get("status") if isinstance(payload, Mapping) else None
        if isinstance(message_value, str) and message_value:
            state.ready_event.set()
        elif isinstance(status_value, str) and status_value:
            state.ready_event.set()

    def _handle_payload(self, state: _StreamState, payload: Mapping[str, Any]) -> None:
        self._mark_stream_ready(state, payload)
        message_type_raw = None
        if isinstance(payload, Mapping):
            message_type_raw = payload.get("type") or payload.get("message")
        message_type = str(message_type_raw).lower() if message_type_raw else ""

        if message_type in {"partial", "partial_transcript", "addpartialtranscript"}:
            self._handle_partial(state, payload)
        elif message_type in {"final", "transcript", "addtranscript", "endoftranscript"}:
            self._handle_final(state, payload)
        elif message_type in {"error", "error_message"}:
            code = str(payload.get("code") or payload.get("error_code") or "error")
            reason = str(payload.get("reason") or payload.get("message") or "unknown")
            self._handle_error(state, code, reason)
        else:
            # Treat unknown messages as potential partials/finals using heuristics.
            text = _extract_text(payload)
            if not text:
                return
            if message_type:
                if "partial" in message_type:
                    self._handle_partial(state, payload)
                    return
                if "final" in message_type or "transcript" in message_type:
                    self._handle_final(state, payload)
                    return
            # Default to partial.
            self._handle_partial(state, payload)

    def _handle_partial(self, state: _StreamState, payload: Mapping[str, Any]) -> None:
        text = _extract_text(payload)
        if not text:
            return
        state.partial_count += 1
        ts_ms = _now_ms()
        metadata = {"ts": ts_ms}
        try:
            _invoke_callback(state.on_partial, text, metadata)
        except Exception:
            self._logger.exception("evt=sm_partial_callback_failed sid=%s", state.sid)
        now = time.monotonic()
        if now - state.last_partial_log >= 0.5:
            state.last_partial_log = now
            latency = _latency_ms(state, now)
            self._logger.info(
                "asr_partial vendor=speechmatics chars=%d latency_ms=%d",
                len(text),
                latency,
            )

    def _handle_final(self, state: _StreamState, payload: Mapping[str, Any]) -> None:
        text = _extract_text(payload)
        if not text:
            return
        state.final_count += 1
        ts_ms = _now_ms()
        metadata = {"ts": ts_ms}
        try:
            _invoke_callback(state.on_final, text, metadata)
        except Exception:
            self._logger.exception("evt=sm_final_callback_failed sid=%s", state.sid)
        now = time.monotonic()
        latency = _latency_ms(state, now)
        self._logger.info(
            "asr_final vendor=speechmatics chars=%d latency_ms=%d",
            len(text),
            latency,
        )

    def _handle_error(self, state: _StreamState, code: str, reason: str) -> None:
        if not state.ready_event.is_set():
            state.ready_error = code
            state.ready_error_detail = reason
            state.ready_event.set()
        self._logger.error(
            "asr_error vendor=speechmatics code=%s reason=%s",
            code,
            reason,
        )
        try:
            _invoke_callback(state.on_error, code, reason)
        except Exception:
            self._logger.exception("evt=sm_error_callback_failed sid=%s", state.sid)
        if not state.closing:
            state.closing = True
            try:
                state.audio_queue.put_nowait(_AUDIO_SENTINEL)
            except Exception:
                pass
            state.loop.create_task(self._shutdown(state))

    async def _shutdown(self, state: _StreamState) -> None:
        try:
            # If you don’t track seq numbers yet, just close the socket cleanly:
            await state.websocket.close()
            # (Optional later: {"message":"EndOfStream","last_seq_no": N})
        except Exception:
            self._logger.debug(
                "evt=sm_stop_send_failed sid=%s stream_id=%s", state.sid, state.stream_id
            )
        await self._close_websocket(state)
        await self._finalize_close(state, None, None)

    async def _close_websocket(self, state: _StreamState) -> None:
        try:
            await state.websocket.close()
        except Exception:
            self._logger.debug(
                "evt=sm_ws_close_failed sid=%s stream_id=%s", state.sid, state.stream_id
            )

    async def _finalize_close(
        self, state: _StreamState, code: int | None, reason: str | None
    ) -> None:
        if state.closed:
            return
        state.closed = True
        if not state.ready_event.is_set():
            state.ready_error = state.ready_error or "connection_closed"
            state.ready_error_detail = state.ready_error_detail or (reason or "")
            state.ready_event.set()
        current = self._streams.get(state.sid)
        if current is state:
            self._streams.pop(state.sid, None)
        if state.on_close is not None:
            try:
                _invoke_callback(state.on_close, code, reason)
            except Exception:
                self._logger.exception("evt=sm_close_callback_failed sid=%s", state.sid)
        duration_ms = self._duration_ms(state)
        self._logger.info(
            "asr_rollup vendor=speechmatics partials=%d finals=%d bytes=%d duration_ms=%d",
            state.partial_count,
            state.final_count,
            state.bytes_sent,
            duration_ms,
        )

    async def _finalize_sender(self, state: _StreamState) -> None:
        state.sender_task = None
        if not state.closing:
            state.closing = True
            try:
                state.audio_queue.put_nowait(_AUDIO_SENTINEL)
            except Exception:
                pass

    async def _finalize_receiver(self, state: _StreamState) -> None:
        state.receiver_task = None
        if not state.closing:
            state.closing = True
            try:
                state.audio_queue.put_nowait(_AUDIO_SENTINEL)
            except Exception:
                pass

    def _duration_ms(self, state: _StreamState) -> int:
        end = state.last_audio_ts or time.monotonic()
        duration = int(max(0.0, (end - state.opened_monotonic) * 1000))
        return duration


__all__ = ["SpeechmaticsClient"]

