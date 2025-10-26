"""Runtime that bridges EVT_NLG events to ElevenLabs streaming synthesis."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future as ThreadFuture, CancelledError as ThreadCancelledError
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from app.telemetry import bus
from app.voice_v2 import EVT_NLG, EVT_TTS_END
from app.voice_v2.engine import EngineV2
from app.voice_v2.tts_base import (
    ProviderCircuitOpenError,
    ProviderTimeoutError,
    TTSProviderBase,
)
from app.services.tts.elevenlabs_client import ElevenLabsStream, ElevenLabsTTSProvider

_log = logging.getLogger(__name__)

_provider_ready_emitted = False
_CHUNK_LOG_INTERVAL_S = 0.5
_FIRST_CHUNK_TIMEOUT_S = 6.0
_PCM_FRAME_BYTES = 2  # 16-bit mono => 2 bytes per PCM frame


@dataclass
class _SynthesisState:
    """Track an in-flight synthesis task for a session."""

    utt_id: str
    post_hold_ms: int
    emit_end: bool = True
    task: asyncio.Task[Any] | ThreadFuture[Any] | None = None
    stream: ElevenLabsStream | None = None
    pcm_buffer: bytearray = field(default_factory=bytearray)


class TTSRuntime:
    """Subscribe to NLG telemetry and orchestrate server-side TTS."""

    def __init__(
        self,
        *,
        engine: EngineV2,
        provider: TTSProviderBase | None = None,
        telemetry_bus=bus,
    ) -> None:
        if engine is None:
            raise ValueError("engine must be provided")

        self._engine = engine
        self._bus = telemetry_bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._default_post_hold_ms = self._load_default_post_hold()
        self._utt_seq: Dict[str, int] = {}
        self._states: Dict[str, _SynthesisState] = {}
        self._subscriptions: list[str] = []

        if provider is None:
            provider = self._build_provider()
        self._provider = provider

        if self._provider is None:
            _log.info("evt=tts_runtime_disabled reason=missing_api_key")
            return

        self._emit_provider_ready(self._provider)

        self._subscriptions.append(self._bus.subscribe(EVT_NLG, self._handle_nlg_event))
        self._subscriptions.append(self._bus.subscribe(EVT_TTS_END, self._handle_tts_end_event))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _handle_nlg_event(self, event: Dict[str, Any]) -> None:
        provider = self._provider
        if provider is None:
            return

        sid = event.get("sid")
        text = event.get("text")
        req_id = event.get("req_id")

        if not isinstance(sid, str) or not sid:
            return
        if not isinstance(text, str) or not text.strip():
            return
        if not isinstance(req_id, str) or not req_id:
            req_id = f"req-{uuid.uuid4().hex}"

        loop = self._ensure_loop()
        if loop is None:
            _log.warning("evt=tts_loop_missing sid=%s", sid)
            return

        utt_id = self._next_utt_id(sid)
        post_hold_ms = self._resolve_post_hold_ms()
        voice_id = self._resolve_voice_id(provider)

        if voice_id is None:
            _log.warning("evt=tts_voice_unresolved sid=%s utt_id=%s", sid, utt_id)
            return

        self._cancel_state(sid, reason="superseded")

        state = _SynthesisState(utt_id=utt_id, post_hold_ms=post_hold_ms)
        self._states[sid] = state

        coroutine = self._run_synthesis(sid, req_id, text, voice_id, state)

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is loop:
            task = loop.create_task(
                coroutine,
                name=f"tts-runtime-{sid}-{utt_id}",
            )
        else:
            task = asyncio.run_coroutine_threadsafe(coroutine, loop)

        state.task = task
        task.add_done_callback(lambda task, sid=sid, state=state: self._on_task_done(sid, state, task))

    def _handle_tts_end_event(self, event: Dict[str, Any]) -> None:
        sid = event.get("sid")
        if not isinstance(sid, str) or not sid:
            return

        state = self._states.get(sid)
        if state is None:
            return

        utt_meta = self._extract_utt_id(event.get("meta"))
        if utt_meta != state.utt_id:
            return

        if event.get("reason") == "canceled":
            state.emit_end = False
            task = state.task
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------
    # Synthesis lifecycle
    # ------------------------------------------------------------------
    async def _run_synthesis(
        self,
        sid: str,
        req_id: str,
        text: str,
        voice_id: Optional[str],
        state: _SynthesisState,
    ) -> None:
        provider = self._provider
        if provider is None:
            return

        stream: ElevenLabsStream | None = None
        chunk_bytes: int | None = None
        start_emitted = False
        total_bytes = 0
        start_time_ms = 0.0
        last_chunk_log = 0.0
        interval_bytes = 0
        interval_chunks = 0
        text_chars = len(text)
        first_chunk_received = False

        def _flush_chunk_log(*, force: bool = False) -> None:
            nonlocal last_chunk_log, interval_bytes, interval_chunks
            if interval_chunks <= 0:
                return
            now = time.monotonic()
            if not force and now - last_chunk_log < _CHUNK_LOG_INTERVAL_S:
                return
            last_chunk_log = now
            _log.debug(
                "evt=tts_chunk sid=%s utt_id=%s chunks=%d bytes=%d total_bytes=%d",
                sid,
                state.utt_id,
                interval_chunks,
                interval_bytes,
                total_bytes,
            )
            interval_bytes = 0
            interval_chunks = 0

        try:
            stream = await provider.synthesize(text, voice_id=voice_id)
            state.stream = stream
        except ProviderCircuitOpenError:
            _log.warning(
                "evt=tts_provider_circuit_open sid=%s vendor=%s", sid, getattr(provider, "vendor", "unknown")
            )
            return
        except ProviderTimeoutError:
            _log.warning(
                "evt=tts_provider_timeout sid=%s vendor=%s", sid, getattr(provider, "vendor", "unknown")
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            reason = _safe_reason(exc)
            _log.error(
                "evt=tts_synthesis_failed reason=provider_error sid=%s utt_id=%s detail=%s",
                sid,
                state.utt_id,
                reason,
                exc_info=True,
            )
            return

        if self._states.get(sid) is not state:
            if stream is not None:
                await stream.aclose()
            return

        try:
            self._engine.on_tts_start(sid, state.utt_id, state.post_hold_ms)
            note_start = getattr(self._bus, "note_tts_start", None)
            if callable(note_start):
                note_start(sid, state.utt_id)
            start_time_ms = time.monotonic()
            _log.info(
                "evt=tts_start sid=%s utt_id=%s voice_id=%s text_chars=%d",
                sid,
                state.utt_id,
                voice_id,
                text_chars,
            )
            start_emitted = True

            iterator = stream.__aiter__()
            chunk_bytes = getattr(stream, "chunk_bytes", None)
            if isinstance(chunk_bytes, int) and chunk_bytes > 0:
                chunk_bytes = chunk_bytes - (chunk_bytes % _PCM_FRAME_BYTES)
                if chunk_bytes <= 0:
                    chunk_bytes = None
            else:
                chunk_bytes = None
            first_chunk_received = False
            while True:
                try:
                    next_chunk = await (
                        asyncio.wait_for(anext(iterator), timeout=_FIRST_CHUNK_TIMEOUT_S)
                        if not first_chunk_received
                        else anext(iterator)
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    _log.error(
                        "evt=tts_synthesis_failed reason=no_chunks_timeout sid=%s utt_id=%s",
                        sid,
                        state.utt_id,
                    )
                    state.emit_end = False
                    break

                if not next_chunk:
                    continue

                buffer = state.pcm_buffer
                buffer.extend(next_chunk)

                if chunk_bytes:
                    while len(buffer) >= chunk_bytes:
                        emitted = bytes(buffer[:chunk_bytes])
                        del buffer[:chunk_bytes]
                        self._engine.emit_tts_audio_chunk(sid, emitted)
                        chunk_len = len(emitted)
                        total_bytes += chunk_len
                        interval_bytes += chunk_len
                        interval_chunks += 1
                        if not first_chunk_received:
                            first_chunk_received = True
                        _flush_chunk_log()
                else:
                    frame_aligned = len(buffer) - (len(buffer) % _PCM_FRAME_BYTES)
                    if frame_aligned >= _PCM_FRAME_BYTES:
                        emitted = bytes(buffer[:frame_aligned])
                        del buffer[:frame_aligned]
                        self._engine.emit_tts_audio_chunk(sid, emitted)
                        chunk_len = len(emitted)
                        total_bytes += chunk_len
                        interval_bytes += chunk_len
                        interval_chunks += 1
                        if not first_chunk_received:
                            first_chunk_received = True
                        _flush_chunk_log()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            reason = _safe_reason(exc)
            _log.error(
                "evt=tts_synthesis_failed reason=provider_error sid=%s utt_id=%s detail=%s",
                sid,
                state.utt_id,
                reason,
                exc_info=True,
            )
            state.emit_end = False
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception:  # pragma: no cover - defensive
                    _log.debug("evt=tts_stream_close_failed", exc_info=True)
            buffer = state.pcm_buffer
            if buffer:
                frame_aligned = len(buffer) - (len(buffer) % _PCM_FRAME_BYTES)
                if frame_aligned >= _PCM_FRAME_BYTES:
                    if chunk_bytes:
                        remaining = frame_aligned
                        while remaining > 0:
                            emit_len = min(remaining, chunk_bytes)
                            emit_len -= emit_len % _PCM_FRAME_BYTES
                            if emit_len <= 0:
                                break
                            emitted = bytes(buffer[:emit_len])
                            del buffer[:emit_len]
                            remaining -= emit_len
                            self._engine.emit_tts_audio_chunk(sid, emitted)
                            chunk_len = len(emitted)
                            total_bytes += chunk_len
                            interval_bytes += chunk_len
                            interval_chunks += 1
                            _flush_chunk_log()
                            first_chunk_received = True
                    else:
                        emitted = bytes(buffer[:frame_aligned])
                        del buffer[:frame_aligned]
                        self._engine.emit_tts_audio_chunk(sid, emitted)
                        chunk_len = len(emitted)
                        total_bytes += chunk_len
                        interval_bytes += chunk_len
                        interval_chunks += 1
                        _flush_chunk_log()
                        first_chunk_received = True
                remainder = len(buffer)
                if remainder:
                    _log.warning(
                        "evt=tts_pcm_bytes_dropped sid=%s utt_id=%s bytes=%d",
                        sid,
                        state.utt_id,
                        remainder,
                    )
                buffer.clear()
            _flush_chunk_log(force=True)
            if start_emitted and state.emit_end:
                self._engine.on_tts_end(sid, state.utt_id, state.post_hold_ms)
                duration_ms = int((time.monotonic() - start_time_ms) * 1000) if start_time_ms else 0
                _log.info(
                    "evt=tts_end sid=%s utt_id=%s total_bytes=%d duration_ms=%d",
                    sid,
                    state.utt_id,
                    total_bytes,
                    duration_ms,
                )
            if start_emitted:
                note_end = getattr(self._bus, "note_tts_end", None)
                if callable(note_end):
                    note_end(sid, state.utt_id)

    def _on_task_done(self, sid: str, state: _SynthesisState, task: asyncio.Task[Any]) -> None:
        current = self._states.get(sid)
        if current is state:
            self._states.pop(sid, None)

        try:
            task.result()
        except (asyncio.CancelledError, ThreadCancelledError):
            pass
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=tts_task_failed sid=%s", sid)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_provider(self) -> TTSProviderBase | None:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            return None
        try:
            return ElevenLabsTTSProvider(telemetry_bus=self._bus)
        except Exception:  # pragma: no cover - defensive
            _log.exception("evt=tts_provider_init_failed")
            return None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._loop
        if loop is not None and loop.is_running():
            return loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        else:
            self._loop = loop
            return loop

        loop = self._loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_background_loop,
                args=(loop,),
                name="tts-runtime-loop",
                daemon=True,
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread
        return loop

    def _next_utt_id(self, sid: str) -> str:
        current = self._utt_seq.get(sid, 0) + 1
        self._utt_seq[sid] = current
        return f"utt-{current:05d}"

    def _resolve_post_hold_ms(self) -> int:
        snapshot = self._engine.policy_snapshot or {}
        hold: Optional[int] = None
        voice_block = snapshot.get("voice")
        if isinstance(voice_block, Mapping):
            candidate = voice_block.get("post_hold_ms")
            if isinstance(candidate, (int, float)):
                hold = int(candidate)
        if hold is None:
            candidate = snapshot.get("post_hold_ms")
            if isinstance(candidate, (int, float)):
                hold = int(candidate)
        if hold is None or hold < 0:
            hold = self._default_post_hold_ms
        return hold

    def _resolve_voice_id(self, provider: TTSProviderBase) -> Optional[str]:
        default_voice = getattr(provider, "default_voice_id", None)
        if isinstance(default_voice, str) and default_voice:
            return default_voice
        resolver = getattr(self._engine, "_voice_profile", None)
        if callable(resolver):
            try:
                voice_id, _ = resolver()
                if isinstance(voice_id, str) and voice_id:
                    return voice_id
            except Exception:  # pragma: no cover - defensive
                _log.debug("evt=tts_voice_profile_resolve_failed", exc_info=True)
        return None

    def _emit_provider_ready(self, provider: TTSProviderBase) -> None:
        global _provider_ready_emitted
        if _provider_ready_emitted:
            return
        vendor = getattr(provider, "vendor", None) or provider.__class__.__name__.lower()
        _log.info("evt=tts_provider_ready provider=%s", vendor)
        _provider_ready_emitted = True

    def _cancel_state(self, sid: str, *, reason: str) -> None:
        state = self._states.pop(sid, None)
        if state is None:
            return
        state.emit_end = False
        task = state.task
        if task is not None and not task.done():
            task.cancel()
        try:
            self._engine.cancel_current_tts(sid, reason=reason)
        except Exception:  # pragma: no cover - defensive
            _log.debug("evt=tts_engine_cancel_failed sid=%s", sid, exc_info=True)

    @staticmethod
    def _extract_utt_id(meta: Any) -> Optional[str]:
        if not isinstance(meta, Mapping):
            return None
        tts_meta = meta.get("tts")
        if not isinstance(tts_meta, Mapping):
            return None
        utt_id = tts_meta.get("utt_id")
        if isinstance(utt_id, str):
            return utt_id
        return None

    def _load_default_post_hold(self) -> int:
        raw = os.getenv("TTS_POST_HOLD_MS")
        if raw is None:
            return 200
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            _log.warning("evt=tts_post_hold_invalid raw=%r", raw)
            return 200
        return max(0, value)

    @staticmethod
    def _run_background_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()


def _safe_reason(exc: BaseException) -> str:
    message = str(exc) or exc.__class__.__name__
    return message.replace('"', "'")


__all__ = ["TTSRuntime"]
