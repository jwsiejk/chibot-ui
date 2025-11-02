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

from app.voice_v2 import EVT_ASR_FINAL, EVT_ASR_PARTIAL

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

    def _from_mapping(candidate: Mapping[str, Any]) -> str | None:
        for key in ("transcript", "text", "partial"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value

        # Speechmatics sometimes returns tokenized content lists.
        content = candidate.get("content") or candidate.get("tokens")
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    token = item.get("value")
                    if not isinstance(token, str) or not token.strip():
                        token = item.get("text")
                    if isinstance(token, str):
                        pieces.append(token)
            combined = "".join(pieces).strip()
            if combined:
                return combined

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
                        text = _from_mapping(alt)
                        if text:
                            return text
            text = _from_mapping(result)
            if text:
                return text

    # Some messages may embed text under "alternatives" directly.
    alternatives = payload.get("alternatives")
    if isinstance(alternatives, list):
        for alt in alternatives:
            if isinstance(alt, Mapping):
                text = _from_mapping(alt)
                if text:
                    return text

    # Metadata blobs sometimes carry the transcript/partial text.
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        text = _from_mapping(metadata)
        if text:
            return text

    # Some messages may embed text directly.
    # IMPORTANT: Do NOT treat vendor "message" (e.g. "AddPartialTranscript") as transcript text.
    direct_keys = ("transcript", "text", "partial")
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return None


def _coerce_transcript_text(payload: Mapping[str, Any]) -> str:
    """Normalize transcript text from vendor payloads.

    Speechmatics transcript messages sometimes omit the top-level
    ``transcript`` field and instead provide tokenized content inside
    nested results.  Prior to this helper we attempted to read only the
    explicit ``transcript`` fields which meant we silently discarded
    perfectly valid finals that only contained token lists.  Falling back
    to the general ``_extract_text`` logic ensures we preserve those
    finals while keeping the existing fast-path for simple payloads.
    """

    text = ""
    transcript = payload.get("transcript")
    if isinstance(transcript, str):
        text = transcript

    if not text:
        results = payload.get("results")
        if isinstance(results, list) and results:
            first_result = results[0] if isinstance(results[0], Mapping) else None
            if isinstance(first_result, Mapping):
                alternatives = first_result.get("alternatives")
                if isinstance(alternatives, list) and alternatives:
                    first_alt = alternatives[0] if isinstance(alternatives[0], Mapping) else None
                    if isinstance(first_alt, Mapping):
                        candidate = first_alt.get("transcript")
                        if isinstance(candidate, str):
                            text = candidate
                if not text:
                    candidate = first_result.get("transcript")
                    if isinstance(candidate, str):
                        text = candidate

    if not text:
        fallback = _extract_text(payload)
        if isinstance(fallback, str):
            text = fallback

    return text


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


def _is_fatal_concurrency_notice(payload: Mapping[str, Any] | None) -> bool:
    """Return True when a Speechmatics concurrency notice is fatal."""

    if not isinstance(payload, Mapping):
        return True

    severity_candidates = (
        payload.get("severity"),
        payload.get("level"),
        payload.get("message"),
    )
    for candidate in severity_candidates:
        if isinstance(candidate, str) and candidate.strip():
            normalized = candidate.strip().lower()
            if normalized in {"error", "critical", "fatal"}:
                return True
            if normalized in {"warning", "warn", "info", "informational"}:
                return False
    # Default to non-fatal so that benign vendor telemetry does not tear down the
    # stream. Hard failures will include an explicit error severity or will result
    # in the connection closing, which is handled elsewhere.
    return False


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
    vendor_notices: set[str] = field(default_factory=set)
    end_of_stream_sent: bool = False


class SpeechmaticsClient:
    """Client wrapper responsible for Speechmatics realtime streams."""

    def __init__(self, api_key: str, url: str, bus: Any, logger: logging.Logger) -> None:
        self._api_key = api_key
        self._url = url
        self._bus = bus
        self._logger = logger or logging.getLogger(__name__)
        self._streams: Dict[str, _StreamState] = {}

    async def _await_capacity(self, new_sid: str, timeout: float = 5.0) -> None:
        if not self._streams:
            return

        deadline = time.monotonic() + max(0.0, timeout)
        while self._streams:
            active_sids = [sid for sid in self._streams if sid != new_sid]
            if not active_sids:
                break

            for existing_sid in active_sids:
                state = self._streams.get(existing_sid)
                if state is None:
                    continue
                if not state.closing:
                    self._logger.warning(
                        "evt=sm_concurrency_guard closing_sid=%s new_sid=%s",
                        existing_sid,
                        new_sid,
                    )
                    state.closing = True
                    try:
                        state.audio_queue.put_nowait(_AUDIO_SENTINEL)
                    except Exception:
                        pass
                    state.loop.create_task(self._shutdown(state))

            if not self._streams:
                break

            if time.monotonic() >= deadline:
                raise RuntimeError("speechmatics_concurrency_guard_timeout")

            await asyncio.sleep(0.05)

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

        await self._await_capacity(new_sid=sid)

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

                if isinstance(payload, Mapping):
                    message_name = payload.get("message")
                else:
                    message_name = None

                if message_name == "AddTranscript":
                    text = _coerce_transcript_text(payload)

                    if not text.strip():
                        self._logger.debug(
                            "evt=sm_empty_transcript_ignored"
                        )
                        continue

                    type_field = payload.get("type")
                    is_final = True
                    if isinstance(type_field, str):
                        normalized_type = type_field.strip().lower()
                        if normalized_type == "partial":
                            is_final = False
                        elif normalized_type == "final":
                            is_final = True
                    if "is_final" in payload:
                        is_final = bool(payload.get("is_final"))
                    elif "final" in payload:
                        # Some payloads use "final": 0/1 to indicate the status.
                        final_flag = payload.get("final")
                        if isinstance(final_flag, (bool, int)):
                            is_final = bool(final_flag)

                    self._emit_transcript_event(state, text, is_final)

                    if is_final:
                        self._handle_final(state, payload, text_override=text, emit_event=False)
                    else:
                        self._handle_partial(state, payload, text_override=text, emit_event=False)
                    continue

                notice_type = ""
                if isinstance(payload, Mapping):
                    raw_notice = payload.get("type") or payload.get("notice")
                    if isinstance(raw_notice, str):
                        notice_type = raw_notice.strip().lower()

                if notice_type.startswith("concurrent_session"):
                    if _is_fatal_concurrency_notice(payload):
                        self._logger.error(
                            "evt=sm_concurrency_notice sid=%s stream_id=%s notice=%s",
                            state.sid,
                            state.stream_id,
                            notice_type,
                        )
                        self._handle_error(state, "concurrent_session", notice_type)
                    else:
                        self._logger.warning(
                            "evt=sm_concurrency_notice sid=%s stream_id=%s notice=%s severity=benign",
                            state.sid,
                            state.stream_id,
                            notice_type,
                        )
                    continue

                if isinstance(message_name, str) and message_name in {
                    "Info",
                    "RecognitionStarted",
                    "AudioAdded",
                }:
                    if message_name not in state.vendor_notices:
                        self._logger.info("evt=sm_notice message=%s", message_name.lower())
                        state.vendor_notices.add(message_name)
                    # Treat vendor notices as sufficient to consider the stream ready so
                    # that the sender loop can proceed with audio transmission.
                    self._mark_stream_ready(state, payload)
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

    def _emit_transcript_event(
        self, state: _StreamState, text: str, is_final: bool
    ) -> None:
        # Drop vendor sentinel names if they ever slip through
        if text.strip().lower() in ("addpartialtranscript", "addtranscript"):
            return
        event_type = EVT_ASR_FINAL if is_final else EVT_ASR_PARTIAL
        meta: Dict[str, Any] = {
            "text": text,
            "vendor": "speechmatics",
        }
        if state.stream_id:
            meta["stream_id"] = state.stream_id
        event = {
            "type": event_type,
            "sid": state.sid,
            "text": text,
            "vendor": "speechmatics",
            "meta": meta,
            "source": "speechmatics_client",
        }
        try:
            self._bus.publish(event)
        except Exception:
            self._logger.exception(
                "evt=sm_bus_publish_failed sid=%s event=%s", state.sid, event_type
            )
        else:
            log_event = "evt=sm_final" if is_final else "evt=sm_partial"
            self._logger.info("%s text_chars=%d", log_event, len(text))

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

    def _handle_partial(
        self,
        state: _StreamState,
        payload: Mapping[str, Any],
        *,
        text_override: str | None = None,
        emit_event: bool = True,
    ) -> None:
        text = text_override if text_override else _extract_text(payload)
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
        if emit_event:
            self._emit_transcript_event(state, text, is_final=False)

    def _handle_final(
        self,
        state: _StreamState,
        payload: Mapping[str, Any],
        *,
        text_override: str | None = None,
        emit_event: bool = True,
    ) -> None:
        text = text_override if text_override else _extract_text(payload)
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
        if emit_event:
            self._emit_transcript_event(state, text, is_final=True)

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
            await self._send_end_of_stream(state)
        except Exception:
            self._logger.debug(
                "evt=sm_stop_send_failed sid=%s stream_id=%s", state.sid, state.stream_id
            )
        await self._close_websocket(state)
        await self._finalize_close(state, None, None)

    async def _send_end_of_stream(self, state: _StreamState) -> None:
        if state.end_of_stream_sent or state.closed:
            return

        # Ensure the sender loop has an opportunity to flush pending audio prior to
        # signalling the vendor that no more data will arrive.
        sender_task = state.sender_task
        if sender_task is not None and not sender_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(sender_task), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            except Exception:
                self._logger.debug(
                    "evt=sm_wait_sender_failed sid=%s stream_id=%s",
                    state.sid,
                    state.stream_id,
                )

        payload = json.dumps({"message": "EndOfStream"})
        try:
            await state.websocket.send(payload)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            self._logger.debug(
                "evt=sm_end_of_stream_send_failed sid=%s stream_id=%s",
                state.sid,
                state.stream_id,
            )
        finally:
            state.end_of_stream_sent = True

    async def _close_websocket(self, state: _StreamState) -> None:
        try:
            await state.websocket.close()
            await state.websocket.wait_closed()
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

