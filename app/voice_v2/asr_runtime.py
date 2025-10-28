"""Deepgram-backed ASR runtime for the voice v2 engine."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional

from app.config import (
    ASR_BACKPRESSURE_THRESHOLD_BYTES,
    ASR_IDLE_CLOSE_MS,
    ASR_TRACE,
)
from app.telemetry import bus
from app.voice_v2 import (
    EVT_ASR_FINAL,
    EVT_ASR_OPEN,
    EVT_ASR_PARTIAL,
    EVT_ASR_READY,
    EVT_WS_JSON_SEND,
)
from app.voice_v2.engine import EngineV2
from app.services.streaming_asr.deepgram_client import DeepgramClient

_log = logging.getLogger(__name__)

_DG_LISTEN_URL = "wss://api.deepgram.com/v1/listen"
_PARTIAL_CONFIDENCE = 0.55
_FINAL_CONFIDENCE = 0.9
_DEFAULT_IDLE_CLOSE_MS = 4000
_BACKPRESSURE_THRESHOLD = max(0, ASR_BACKPRESSURE_THRESHOLD_BYTES)
_TRACE_ENABLED = bool(ASR_TRACE)
_STREAM_OPEN_TIMEOUT_S = 5.0
_NO_AUDIO_TIMEOUT_S = 9.0
_LISTENING_STATE = "Listening"
_READY_STATES = {"Ready", "Idle"}


@dataclass
class _SessionState:
    """Track per-session streaming state."""

    sid: str
    pending: Deque[bytes] = field(default_factory=deque)
    buffered_bytes: int = 0
    req_id: Optional[str] = None
    stream_open_task: Optional[asyncio.Task[None]] = None
    stream_open: bool = False
    last_audio_ts: float = 0.0
    idle_handle: asyncio.TimerHandle | None = None
    stream_id: Optional[str] = None
    close_reason: Optional[str] = None
    opened_at_ms: int = 0
    opened_at_monotonic: float = 0.0
    last_audio_ts_ms: int = 0
    chunks_sent: int = 0
    bytes_sent: int = 0
    dg_msgs_partial: int = 0
    dg_msgs_final: int = 0
    finals_delivered: int = 0
    dropped_chunks: int = 0
    last_stream_id: Optional[str] = None
    ready_watchdog: asyncio.TimerHandle | None = None
    ready_armed_at: float = 0.0
    prearm_requested: bool = False

    def __post_init__(self) -> None:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        self.opened_at_monotonic = now_monotonic
        self.opened_at_ms = int(now_wall * 1000)
        self.last_audio_ts = now_monotonic
        self.last_audio_ts_ms = int(now_wall * 1000)


class ASRRuntime:
    """Bridge websocket audio to Deepgram realtime transcription."""

    def __init__(
        self,
        engine: EngineV2,
        client: DeepgramClient,
        *,
        telemetry_bus: Any | None = None,
    ) -> None:
        if engine is None:
            raise ValueError("engine must be provided")
        if client is None:
            raise ValueError("client must be provided")

        self._engine = engine
        self._client = client
        self._bus = telemetry_bus or bus
        self._configure_client()
        self._sessions: Dict[str, _SessionState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        configured_idle = ASR_IDLE_CLOSE_MS
        if not isinstance(configured_idle, (int, float)):
            configured_idle = _DEFAULT_IDLE_CLOSE_MS
        client_idle = getattr(client, "idle_close_ms", None)
        if isinstance(client_idle, (int, float)) and client_idle >= 0:
            self._idle_close_ms = int(client_idle)
        else:
            self._idle_close_ms = int(configured_idle)
        if self._idle_close_ms < 0:
            self._idle_close_ms = _DEFAULT_IDLE_CLOSE_MS

    def set_bus(self, telemetry_bus: Any | None) -> None:
        if telemetry_bus is None:
            return
        self._bus = telemetry_bus

    def _configure_client(self) -> None:
        """Force the Deepgram client to use the containerized listen URL."""

        target_url = _DG_LISTEN_URL
        current_url = getattr(self._client, "_url", None)
        if current_url == target_url:
            return
        try:
            setattr(self._client, "_url", target_url)
        except Exception:  # pragma: no cover - defensive
            _log.debug(
                "evt=asr_runtime_set_listen_url_failed target_url=%s", target_url,
                exc_info=True,
            )
        else:
            _log.debug(
                "evt=asr_runtime_listen_url_set url=%s", target_url
            )

    # ------------------------------------------------------------------
    # Websocket hooks
    # ------------------------------------------------------------------
    def on_ws_open(self, sid: str) -> None:
        self._ensure_loop()
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state
        _log.info("evt=asr_on_ws_open sid=%s", sid)
        if not state.stream_open:
            state.prearm_requested = True
            self._ensure_stream(sid, state)

    def on_ws_audio(self, sid: str, chunk: bytes) -> None:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        data = bytes(chunk)
        if not data:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        now_monotonic = time.monotonic()
        state.last_audio_ts = now_monotonic
        state.last_audio_ts_ms = int(time.time() * 1000)
        if state.ready_watchdog is not None and state.ready_armed_at:
            self._cancel_ready_watchdog(state)
        chunk_len = len(data)

        if state.stream_id is None:
            state.stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.last_stream_id = state.stream_id

        if not state.stream_open:
            state.pending.append(data)
            state.buffered_bytes += chunk_len
            self._apply_backpressure(sid, state)
            if state.pending:
                self._ensure_stream(sid, state)
        else:
            self._forward_chunk(sid, state, data)

        if _TRACE_ENABLED:
            _log.debug(
                "evt=asr_audio_enqueued sid=%s stream_id=%s bytes=%d buffered_bytes=%d pending_chunks=%d",
                sid,
                state.stream_id or state.last_stream_id or "",
                chunk_len,
                state.buffered_bytes,
                len(state.pending),
            )

        self._reset_idle_timer(sid, state)

    def on_ws_close(self, sid: str) -> None:
        state = self._sessions.pop(sid, None)
        if state is None:
            return
        state.close_reason = "client_ws_closed"
        self._cancel_idle_timer(state)
        self._cancel_ready_watchdog(state)
        task = state.stream_open_task
        if task is not None and not task.done():
            task.cancel()
        state.stream_open = False
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_stream_close_failed sid=%s", sid)
        finally:
            self._log_session_rollup(state)
        state.req_id = None

    def prearm(self, sid: str) -> None:
        """Request that the ASR stream open proactively for the session."""

        if not isinstance(sid, str) or not sid:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state
        if state.stream_open:
            return

        state.prearm_requested = True
        self._ensure_stream(sid, state)

    async def open_if_needed(self, sid: str, *, req_id: str | None = None) -> None:
        """Ensure the streaming session is open before capturing microphone audio."""

        if not isinstance(sid, str) or not sid:
            return

        loop = self._ensure_loop()
        if loop is None:
            return

        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        if isinstance(req_id, str) and req_id:
            state.req_id = req_id

        if state.stream_open:
            return

        state.prearm_requested = True
        self._ensure_stream(sid, state)

        deadline = time.monotonic() + max(0.0, _STREAM_OPEN_TIMEOUT_S)
        while not state.stream_open:
            task = state.stream_open_task
            now = time.monotonic()
            if task is None or task.done():
                if now >= deadline:
                    break
                self._ensure_stream(sid, state)
                await asyncio.sleep(0.01)
                continue
            remaining = deadline - now
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(task, timeout=remaining)
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._loop
        if loop is not None:
            return loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._loop = loop
        return loop

    def _apply_backpressure(self, sid: str, state: _SessionState) -> None:
        if _BACKPRESSURE_THRESHOLD <= 0:
            return
        if state.buffered_bytes <= _BACKPRESSURE_THRESHOLD:
            return

        dropped_chunks = 0
        while state.pending and state.buffered_bytes > _BACKPRESSURE_THRESHOLD:
            removed = state.pending.popleft()
            state.buffered_bytes = max(0, state.buffered_bytes - len(removed))
            dropped_chunks += 1

        if dropped_chunks:
            state.dropped_chunks += dropped_chunks
            stream_id = state.stream_id or state.last_stream_id or ""
            _log.info(
                "evt=asr_backpressure sid=%s stream_id=%s buffered_bytes=%d threshold=%d action=drop_oldest dropped_chunks=%d",
                sid,
                stream_id,
                state.buffered_bytes,
                _BACKPRESSURE_THRESHOLD,
                dropped_chunks,
            )

    def _log_session_rollup(self, state: _SessionState) -> None:
        stream_id = state.last_stream_id or state.stream_id or ""
        duration_ms = int(
            max(0.0, (time.monotonic() - state.opened_at_monotonic) * 1000)
        )
        _log.info(
            "evt=asr_rollup sid=%s stream_id=%s opened_at_ms=%d last_audio_ts_ms=%d "
            "chunks_sent=%d bytes_sent=%d partials=%d finals=%d finals_delivered=%d "
            "dropped=%d duration_ms=%d",
            state.sid,
            stream_id,
            state.opened_at_ms,
            state.last_audio_ts_ms,
            state.chunks_sent,
            state.bytes_sent,
            state.dg_msgs_partial,
            state.dg_msgs_final,
            state.finals_delivered,
            state.dropped_chunks,
            duration_ms,
        )

    def _log_utterance(
        self,
        state: _SessionState,
        stream_id: str,
        req_id: str,
        metadata: Dict[str, object],
        text: str,
    ) -> None:
        chars = int(metadata.get("len_chars") or len(text))
        words_meta = metadata.get("words")
        if isinstance(words_meta, (int, float)):
            words = max(0, int(words_meta))
        else:
            words = len([w for w in text.strip().split() if w])
        asr_latency_ms = int(metadata.get("latency_ms") or 0)
        log_template = (
            "evt=asr_utterance sid=%s stream_id=%s req_id=%s chars=%d words=%d "
            "asr_latency_ms=%d"
        )
        log_args = [
            state.sid,
            stream_id,
            req_id,
            chars,
            words,
            asr_latency_ms,
        ]
        e2e_latency = metadata.get("e2e_latency_ms")
        if isinstance(e2e_latency, (int, float)):
            log_template += " e2e_latency_ms=%d"
            log_args.append(int(e2e_latency))
        _log.info(log_template, *log_args)

    def _ensure_stream(self, sid: str, state: _SessionState) -> None:
        loop = self._ensure_loop()
        if loop is None:
            _log.warning("evt=asr_no_loop sid=%s", sid)
            return
        if state.stream_open:
            return
        if not state.pending and not state.prearm_requested:
            return
        task = state.stream_open_task
        if task is not None and not task.done():
            return

        async def _open() -> None:
            if state.stream_id is None:
                state.stream_id = f"dg-stream-{uuid.uuid4().hex}"
            stream_id = state.stream_id
            state.last_stream_id = stream_id
            state.close_reason = None
            state.prearm_requested = False
            try:
                policy_snapshot = None
                try:
                    policy_snapshot = getattr(self._engine, "policy_snapshot", None)
                except Exception:  # pragma: no cover - defensive
                    policy_snapshot = None
                qs = await asyncio.wait_for(
                    self._client.open_stream(
                        sid,
                        on_partial=self._make_partial_cb(sid),
                        on_final=self._make_final_cb(sid),
                        on_error=self._make_error_cb(sid),
                        stream_id=stream_id,
                        on_close=self._make_close_cb(sid, state),
                        policy=policy_snapshot,
                    ),
                    timeout=_STREAM_OPEN_TIMEOUT_S,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                _log.error("evt=asr_open_failed sid=%s vendor=deepgram", sid)
                state.stream_id = None
                state.stream_open = False
                state.stream_open_task = None
                loop = self._ensure_loop()
                if loop is not None:
                    loop.call_later(0.1, self._ensure_stream, sid, state)
                return
            except Exception:  # pragma: no cover - defensive
                _log.exception("evt=asr_stream_open_failed sid=%s", sid)
                state.stream_id = None
                return

            state.stream_open = True
            open_event = {"type": EVT_ASR_OPEN, "sid": sid, "vendor": "deepgram"}
            if stream_id:
                open_event["stream_id"] = stream_id
            self._bus.publish(open_event)
            _log.info(
                "evt=asr_session_open sid=%s content_type=%s qs=%s",
                sid,
                "(not set)",
                qs or "",
            )
            stream_id_value = stream_id if isinstance(stream_id, str) and stream_id else ""
            if not stream_id_value and isinstance(state.stream_id, str) and state.stream_id:
                stream_id_value = state.stream_id
            ready_kwargs = {
                "sid": sid,
                "vendor": "deepgram",
                "stream_id": stream_id_value,
            }
            emit = getattr(self._bus, "emit", None)
            if callable(emit):
                try:
                    emit(EVT_ASR_READY, **ready_kwargs)
                except TypeError:
                    emit(EVT_ASR_READY, ready_kwargs)
            ready_event = {"type": EVT_ASR_READY, **ready_kwargs}
            self._bus.publish(ready_event)
            _log.info(
                "evt=asr_ready sid=%s vendor=deepgram stream_id=%s",
                sid,
                stream_id_value,
            )

            input_desc = {"container": "webm", "codec": "opus", "rate_hz": 48000, "channels": 1}
            asr_ready_frame = {
                "type": "asr.ready",
                "vendor": "deepgram",
                "input": input_desc,
            }
            self._bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "frame": asr_ready_frame})
            if stream_id:
                _log.info(
                    'evt=asr_session_open sid=%s stream_id=%s content_type="%s" idle_close_ms=%d',
                    sid,
                    stream_id,
                    "(not set)",
                    int(self._idle_close_ms),
                )
            pre_chunks_sent = state.chunks_sent
            while state.pending:
                chunk = state.pending.popleft()
                chunk_len = len(chunk)
                state.buffered_bytes = max(0, state.buffered_bytes - chunk_len)
                self._forward_chunk(sid, state, chunk)
            if state.chunks_sent == pre_chunks_sent:
                self._start_ready_watchdog(sid, state)
            else:
                self._cancel_ready_watchdog(state)
            state.stream_open_task = None
            self._reset_idle_timer(sid, state)

        def _on_done(_task: asyncio.Task[None]) -> None:
            if state.stream_open_task is _task:
                state.stream_open_task = None

        state.stream_open_task = loop.create_task(_open(), name=f"asr-stream-open-{sid}")
        state.stream_open_task.add_done_callback(_on_done)

    def _forward_chunk(self, sid: str, state: _SessionState, chunk: bytes) -> None:
        if not chunk:
            return
        chunk_len = len(chunk)
        state.chunks_sent += 1
        state.bytes_sent += chunk_len
        self._client.send_audio(sid, chunk)

    def _make_partial_cb(self, sid: str) -> Callable[[str, Dict[str, object]], None]:
        def _callback(text: str, metadata: Dict[str, object] | None = None) -> None:
            self._on_partial(sid, text, metadata or {})

        return _callback

    def _make_final_cb(self, sid: str) -> Callable[[str, Dict[str, object]], None]:
        def _callback(text: str, metadata: Dict[str, object] | None = None) -> None:
            self._on_final(sid, text, metadata or {})

        return _callback

    def _make_error_cb(self, sid: str) -> Callable[[str], None]:
        def _callback(error: str) -> None:
            self._on_error(sid, error)

        return _callback

    def _make_close_cb(self, sid: str, state: _SessionState) -> Callable[[int | None, str | None], None]:
        def _callback(_code: int | None, _reason: str | None) -> None:
            stream_id = state.stream_id
            if not stream_id:
                return
            reason = state.close_reason
            if reason is None:
                reason = "server_shutdown" if _code == 1001 else "error"
            state.last_stream_id = stream_id
            _log.info(
                "evt=asr_session_close sid=%s stream_id=%s reason=%s",
                sid,
                stream_id,
                reason,
            )
            state.stream_id = None
            state.close_reason = None
            state.stream_open = False
            state.req_id = None

        return _callback

    def _on_partial(
        self, sid: str, text: str, metadata: Dict[str, object] | None = None
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        metadata = metadata or {}
        if state.stream_id is None:
            meta_stream = metadata.get("stream_id")
            if isinstance(meta_stream, str) and meta_stream:
                state.stream_id = meta_stream
        stream_id = state.stream_id or ""
        if not stream_id:
            stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.stream_id = stream_id
        state.last_stream_id = stream_id
        state.dg_msgs_partial += 1
        len_chars = int(metadata.get("len_chars") or len(text))
        utterance_id = metadata.get("utterance_id")
        if not utterance_id:
            utterance_id = f"dg-utt-{uuid.uuid4().hex}"
        latency_ms = int(metadata.get("latency_ms") or 0)

        _log.debug(
            "evt=dg_partial sid=%s stream_id=%s len_chars=%d is_final=false "
            "utterance_id=%s latency_ms=%d",
            sid,
            stream_id,
            len_chars,
            utterance_id,
            latency_ms,
        )

        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_session = ensure_session(sid) if callable(ensure_session) else None
        engine_req_id = None
        if engine_session is not None:
            candidate = getattr(engine_session, "req_id", None)
            if isinstance(candidate, str) and candidate:
                engine_req_id = candidate

        if isinstance(engine_req_id, str) and engine_req_id:
            req_id = engine_req_id
            state.req_id = req_id
        else:
            req_id = state.req_id
            if not isinstance(req_id, str) or not req_id:
                req_id = f"dg-{uuid.uuid4().hex}"
                state.req_id = req_id
            if engine_session is not None and getattr(engine_session, "req_id", None) != req_id:
                engine_session.req_id = req_id

        try:
            self._engine.on_asr_partial(sid, req_id, _PARTIAL_CONFIDENCE, text)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_engine_partial_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_PARTIAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _PARTIAL_CONFIDENCE,
            "vendor": "deepgram",
        }
        self._bus.publish(event)

    def _on_final(
        self, sid: str, text: str, metadata: Dict[str, object] | None = None
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        metadata = metadata or {}
        if state.stream_id is None:
            meta_stream = metadata.get("stream_id")
            if isinstance(meta_stream, str) and meta_stream:
                state.stream_id = meta_stream
        stream_id = state.stream_id or ""
        if not stream_id:
            stream_id = f"dg-stream-{uuid.uuid4().hex}"
            state.stream_id = stream_id
        state.last_stream_id = stream_id
        state.dg_msgs_final += 1
        len_chars = int(metadata.get("len_chars") or len(text))
        utterance_id = metadata.get("utterance_id")
        if not utterance_id:
            utterance_id = f"dg-utt-{uuid.uuid4().hex}"
        latency_ms = int(metadata.get("latency_ms") or 0)

        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_session = ensure_session(sid) if callable(ensure_session) else None
        engine_req_id = None
        if engine_session is not None:
            candidate = getattr(engine_session, "req_id", None)
            if isinstance(candidate, str) and candidate:
                engine_req_id = candidate

        if isinstance(engine_req_id, str) and engine_req_id:
            req_id = engine_req_id
        elif isinstance(state.req_id, str) and state.req_id:
            req_id = state.req_id
            if engine_session is not None and getattr(engine_session, "req_id", None) != req_id:
                engine_session.req_id = req_id
        else:
            req_id = f"req-{uuid.uuid4().hex}"
            if engine_session is not None:
                engine_session.req_id = req_id

        state.req_id = None

        _log.debug(
            "evt=dg_final sid=%s stream_id=%s len_chars=%d is_final=true "
            "utterance_id=%s latency_ms=%d req_id=%s",
            sid,
            stream_id,
            len_chars,
            utterance_id,
            latency_ms,
            req_id,
        )

        try:
            self._engine.on_asr_final(sid, text, req_id)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_engine_final_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_FINAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _FINAL_CONFIDENCE,
            "vendor": "deepgram",
        }
        self._bus.publish(event)
        state.finals_delivered += 1
        self._log_utterance(state, stream_id, req_id, metadata, text)

    def _on_error(self, sid: str, error: str) -> None:
        _log.exception("evt=asr_error sid=%s err=%s", sid, error)

        state = self._sessions.get(sid)
        if state is None:
            return
        state.close_reason = "error"
        state.stream_open = False
        state.req_id = None
        self._cancel_idle_timer(state)
        self._cancel_ready_watchdog(state)
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_error_close_failed sid=%s", sid)

    def _cancel_idle_timer(self, state: _SessionState) -> None:
        handle = state.idle_handle
        if handle is not None:
            handle.cancel()
            state.idle_handle = None

    def _cancel_ready_watchdog(self, state: _SessionState) -> None:
        handle = state.ready_watchdog
        if handle is not None:
            handle.cancel()
        state.ready_watchdog = None
        state.ready_armed_at = 0.0

    def _start_ready_watchdog(self, sid: str, state: _SessionState) -> None:
        loop = self._ensure_loop()
        if loop is None:
            return
        self._cancel_ready_watchdog(state)

        def _fire() -> None:
            state.ready_watchdog = None
            armed_at = state.ready_armed_at
            state.ready_armed_at = 0.0
            if armed_at and state.last_audio_ts <= armed_at:
                _log.warning("evt=asr_no_audio_timeout sid=%s", sid)
                self._bus.publish({"type": EVT_ASR_READY, "sid": sid, "vendor": "deepgram"})
                _log.info("evt=asr_ready_published sid=%s vendor=deepgram", sid)
                input_desc = {"container": "webm", "codec": "opus", "rate_hz": 48000, "channels": 1}
                asr_ready_frame = {
                    "type": "asr.ready",
                    "vendor": "deepgram",
                    "input": input_desc,
                }
                self._bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "frame": asr_ready_frame})

        state.ready_armed_at = time.monotonic()
        state.ready_watchdog = loop.call_later(_NO_AUDIO_TIMEOUT_S, _fire)

    def _reset_idle_timer(self, sid: str, state: _SessionState) -> None:
        threshold_ms = self._idle_close_ms
        if threshold_ms <= 0:
            return
        loop = self._ensure_loop()
        if loop is None:
            return
        self._cancel_idle_timer(state)

        delay = threshold_ms / 1000.0

        def _fire() -> None:
            state.idle_handle = None
            self._handle_idle_timeout(sid, state)

        state.idle_handle = loop.call_later(delay, _fire)

    def _handle_idle_timeout(self, sid: str, state: _SessionState) -> None:
        if not state.stream_open:
            return

        state.close_reason = "idle_timeout"
        ensure_session = getattr(self._engine, "_ensure_session", None)
        engine_state = None
        if callable(ensure_session):
            try:
                engine_session = ensure_session(sid)
            except Exception:  # pragma: no cover - defensive
                engine_session = None
            if engine_session is not None:
                engine_state = getattr(engine_session, "state", None)

        close_for_prearm = False
        if engine_state == _LISTENING_STATE:
            if state.chunks_sent == 0 and not state.pending:
                close_for_prearm = True
            else:
                self._reset_idle_timer(sid, state)
                return
        elif engine_state not in _READY_STATES:
            # Re-arm the timer to check again once the engine transitions.
            self._reset_idle_timer(sid, state)
            return

        idle_ms = int(
            max(0.0, (time.monotonic() - state.last_audio_ts) * 1000)
            if state.last_audio_ts
            else self._idle_close_ms
        )
        stream_id = state.stream_id or state.last_stream_id or ""
        if close_for_prearm:
            _log.warning(
                "evt=asr_prearm_idle sid=%s stream_id=%s idle_ms=%d action=close",
                sid,
                stream_id,
                idle_ms,
            )
        else:
            _log.info(
                "evt=asr_idle_close sid=%s stream_id=%s idle_ms=%d",
                sid,
                stream_id,
                idle_ms,
            )
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=asr_idle_close_failed sid=%s", sid)
        state.stream_open = False
        state.last_stream_id = stream_id or state.last_stream_id
        state.stream_id = None
        state.req_id = None

    # Optional idle guard
    def close_idle_streams(self) -> None:
        if self._loop is None:
            return
        now = time.monotonic()
        threshold = self._idle_close_ms / 1000.0
        for sid, state in list(self._sessions.items()):
            if not state.stream_open:
                continue
            if threshold <= 0:
                continue
            if state.last_audio_ts and now - state.last_audio_ts >= threshold:
                state.close_reason = "idle_timeout"
                try:
                    stream_id = state.stream_id or state.last_stream_id or ""
                    idle_ms = int(max(0.0, (now - state.last_audio_ts) * 1000))
                    _log.info(
                        "evt=asr_idle_close sid=%s stream_id=%s idle_ms=%d",
                        sid,
                        stream_id,
                        idle_ms,
                    )
                    self._client.close_stream(sid)
                except Exception:  # pragma: no cover - defensive
                    _log.exception("evt=asr_idle_close_failed sid=%s", sid)
                state.stream_open = False
                state.last_stream_id = state.stream_id or state.last_stream_id
                state.stream_id = None
                state.req_id = None
