"""Deepgram-backed ASR runtime for the voice v2 engine."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional

from app.telemetry import bus
from app.voice_v2 import EVT_ASR_FINAL, EVT_ASR_PARTIAL
from app.voice_v2.engine import EngineV2
from app.services.streaming_asr.deepgram_client import DeepgramClient

_logger = logging.getLogger(__name__)

_CONTENT_TYPE = "audio/webm;codecs=opus"
_PARTIAL_CONFIDENCE = 0.55
_FINAL_CONFIDENCE = 0.9
_DEFAULT_IDLE_CLOSE_MS = 4000


@dataclass
class _SessionState:
    """Track per-session streaming state."""

    sid: str
    pending: Deque[bytes] = field(default_factory=deque)
    req_id: Optional[str] = None
    stream_open_task: Optional[asyncio.Task[None]] = None
    stream_open: bool = False
    last_audio_ts: float = 0.0
    drop_logged: bool = False


class ASRRuntime:
    """Bridge websocket audio to Deepgram realtime transcription."""

    def __init__(self, engine: EngineV2, client: DeepgramClient) -> None:
        if engine is None:
            raise ValueError("engine must be provided")
        if client is None:
            raise ValueError("client must be provided")

        self._engine = engine
        self._client = client
        self._sessions: Dict[str, _SessionState] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._idle_close_ms = getattr(client, "idle_close_ms", _DEFAULT_IDLE_CLOSE_MS)
        if not isinstance(self._idle_close_ms, (int, float)) or self._idle_close_ms < 0:
            self._idle_close_ms = _DEFAULT_IDLE_CLOSE_MS

    # ------------------------------------------------------------------
    # Websocket hooks
    # ------------------------------------------------------------------
    def on_ws_open(self, sid: str) -> None:
        self._ensure_loop()
        self._sessions[sid] = _SessionState(sid=sid)

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

        state.last_audio_ts = time.monotonic()
        if not state.stream_open:
            state.pending.append(data)
            self._ensure_stream(sid, state)
            return

        self._client.send_audio(sid, data)

    def on_ws_close(self, sid: str) -> None:
        state = self._sessions.pop(sid, None)
        if state is None:
            return
        task = state.stream_open_task
        if task is not None and not task.done():
            task.cancel()
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _logger.exception("evt=asr_stream_close_failed sid=%s", sid)

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

    def _ensure_stream(self, sid: str, state: _SessionState) -> None:
        loop = self._ensure_loop()
        if loop is None:
            _logger.warning("evt=asr_no_loop sid=%s", sid)
            return
        if state.stream_open:
            return
        task = state.stream_open_task
        if task is not None and not task.done():
            return

        async def _open() -> None:
            try:
                await self._client.open_stream(
                    sid,
                    _CONTENT_TYPE,
                    on_partial=self._make_partial_cb(sid),
                    on_final=self._make_final_cb(sid),
                    on_error=self._make_error_cb(sid),
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                _logger.exception("evt=asr_stream_open_failed sid=%s", sid)
                return

            state.stream_open = True
            while state.pending:
                chunk = state.pending.popleft()
                self._client.send_audio(sid, chunk)
            state.stream_open_task = None

        def _on_done(_task: asyncio.Task[None]) -> None:
            if state.stream_open_task is _task:
                state.stream_open_task = None

        state.stream_open_task = loop.create_task(_open(), name=f"asr-stream-open-{sid}")
        state.stream_open_task.add_done_callback(_on_done)

    def _make_partial_cb(self, sid: str) -> Callable[[str], None]:
        def _callback(text: str) -> None:
            self._on_partial(sid, text)

        return _callback

    def _make_final_cb(self, sid: str) -> Callable[[str], None]:
        def _callback(text: str) -> None:
            self._on_final(sid, text)

        return _callback

    def _make_error_cb(self, sid: str) -> Callable[[str], None]:
        def _callback(error: str) -> None:
            self._on_error(sid, error)

        return _callback

    def _on_partial(self, sid: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        req_id = state.req_id
        if req_id is None:
            req_id = f"dg-{uuid.uuid4().hex}"
            state.req_id = req_id
        session = getattr(self._engine, "_ensure_session", None)
        if callable(session):
            engine_session = session(sid)
            if getattr(engine_session, "req_id", None) != req_id:
                engine_session.req_id = req_id

        try:
            self._engine.on_asr_partial(sid, req_id, _PARTIAL_CONFIDENCE, text)
        except Exception:  # pragma: no cover - defensive
            _logger.exception("evt=asr_engine_partial_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_PARTIAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _PARTIAL_CONFIDENCE,
            "vendor": "deepgram",
        }
        bus.publish(event)

    def _on_final(self, sid: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        state = self._sessions.get(sid)
        if state is None:
            state = _SessionState(sid=sid)
            self._sessions[sid] = state

        req_id = f"dg-{uuid.uuid4().hex}"
        state.req_id = None

        ensure_session = getattr(self._engine, "_ensure_session", None)
        if callable(ensure_session):
            engine_session = ensure_session(sid)
            engine_session.req_id = req_id

        try:
            self._engine.on_asr_final(sid, text)
        except Exception:  # pragma: no cover - defensive
            _logger.exception("evt=asr_engine_final_failed sid=%s", sid)

        event = {
            "type": EVT_ASR_FINAL,
            "sid": sid,
            "req_id": req_id,
            "text": text,
            "confidence": _FINAL_CONFIDENCE,
            "vendor": "deepgram",
        }
        bus.publish(event)

    def _on_error(self, sid: str, error: str) -> None:
        _logger.exception("evt=asr_error sid=%s err=%s", sid, error)

        state = self._sessions.get(sid)
        if state is None:
            return
        state.stream_open = False
        state.req_id = None
        try:
            self._client.close_stream(sid)
        except Exception:  # pragma: no cover - defensive
            _logger.exception("evt=asr_error_close_failed sid=%s", sid)

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
                try:
                    self._client.close_stream(sid)
                except Exception:  # pragma: no cover - defensive
                    _logger.exception("evt=asr_idle_close_failed sid=%s", sid)
                state.stream_open = False
                state.req_id = None
