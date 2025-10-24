"""Runtime that bridges EVT_NLG events to ElevenLabs streaming synthesis."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
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

_logger = logging.getLogger(__name__)


@dataclass
class _SynthesisState:
    """Track an in-flight synthesis task for a session."""

    utt_id: str
    post_hold_ms: int
    emit_end: bool = True
    task: asyncio.Task[Any] | None = None
    stream: ElevenLabsStream | None = None


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
        self._default_post_hold_ms = self._load_default_post_hold()
        self._utt_seq: Dict[str, int] = {}
        self._states: Dict[str, _SynthesisState] = {}
        self._subscriptions: list[str] = []

        if provider is None:
            provider = self._build_provider()
        self._provider = provider

        if self._provider is None:
            _logger.info("TTS runtime disabled: ELEVENLABS_API_KEY not configured")
            return

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
            _logger.warning("No running event loop for TTS synthesis", extra={"sid": sid})
            return

        utt_id = self._next_utt_id(sid)
        post_hold_ms = self._resolve_post_hold_ms()
        voice_id = self._resolve_voice_id(provider)

        if voice_id is None:
            _logger.warning("Skipping TTS synthesis; no voice_id available", extra={"sid": sid})
            return

        self._cancel_state(sid, reason="superseded")

        state = _SynthesisState(utt_id=utt_id, post_hold_ms=post_hold_ms)
        self._states[sid] = state

        task = loop.create_task(
            self._run_synthesis(sid, req_id, text, voice_id, state),
            name=f"tts-runtime-{sid}-{utt_id}",
        )
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
        start_emitted = False

        try:
            stream = await provider.synthesize(text, voice_id=voice_id)
            state.stream = stream
        except ProviderCircuitOpenError:
            _logger.warning("ElevenLabs circuit open; skipping synthesis", extra={"sid": sid})
            return
        except ProviderTimeoutError:
            _logger.warning("ElevenLabs synthesis timed out", extra={"sid": sid})
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _logger.exception("ElevenLabs synthesis failed", extra={"sid": sid, "error": str(exc)})
            return

        if self._states.get(sid) is not state:
            if stream is not None:
                await stream.aclose()
            return

        try:
            self._engine.on_tts_start(sid, state.utt_id, state.post_hold_ms)
            start_emitted = True

            async for chunk in stream:
                if not chunk:
                    continue
                self._engine.emit_tts_audio_chunk(sid, chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _logger.exception(
                "Error streaming ElevenLabs audio", extra={"sid": sid, "utt_id": state.utt_id, "error": str(exc)}
            )
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except Exception:  # pragma: no cover - defensive
                    _logger.debug("Error closing ElevenLabs stream", exc_info=True)
            if start_emitted and state.emit_end:
                self._engine.on_tts_end(sid, state.utt_id, state.post_hold_ms)

    def _on_task_done(self, sid: str, state: _SynthesisState, task: asyncio.Task[Any]) -> None:
        current = self._states.get(sid)
        if current is state:
            self._states.pop(sid, None)

        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - defensive
            _logger.exception("TTS synthesis task failed", extra={"sid": sid})

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
            _logger.exception("Failed to initialize ElevenLabsTTSProvider")
            return None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._loop
        if loop is not None and loop.is_running():
            return loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._loop = loop
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
        resolver = getattr(self._engine, "_voice_profile", None)
        if callable(resolver):
            try:
                voice_id, _ = resolver()
                if isinstance(voice_id, str) and voice_id:
                    return voice_id
            except Exception:  # pragma: no cover - defensive
                _logger.debug("Unable to resolve voice profile from engine", exc_info=True)
        default_voice = getattr(provider, "default_voice_id", None)
        if isinstance(default_voice, str) and default_voice:
            return default_voice
        return None

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
            _logger.debug("Engine cancel_current_tts failed", exc_info=True)

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
            _logger.warning("Invalid TTS_POST_HOLD_MS=%r; defaulting to 200", raw)
            return 200
        return max(0, value)


__all__ = ["TTSRuntime"]
