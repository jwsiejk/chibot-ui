from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from datetime import datetime, timezone
from time import perf_counter

from app.domain_models import EventRecord, SessionRecord, TimingRecord
from app.events import EventBus
from app.stt import FasterWhisperSttService, SttError
from app.storage import Database
from app.turns import BusyError, TurnManager


class VoiceInputError(RuntimeError):
    pass


class EmptyTranscriptionError(VoiceInputError):
    pass


class InvalidVoiceLifecycleError(VoiceInputError):
    pass


@dataclass
class ActivePttLifecycle:
    started_at: str
    device_id: str | None
    trace_id: str | None


logger = logging.getLogger(__name__)


class VoiceTurnService:
    def __init__(
        self,
        db: Database,
        event_bus: EventBus,
        stt: FasterWhisperSttService,
        turn_manager: TurnManager,
    ) -> None:
        self.db = db
        self.event_bus = event_bus
        self.stt = stt
        self.turn_manager = turn_manager
        self._active_ptt: dict[str, ActivePttLifecycle] = {}
        self._ptt_lock = asyncio.Lock()

    async def begin_ptt(self, session: SessionRecord, *, device_id: str | None, trace_id: str | None = None) -> None:
        current_session = self._get_current_session(session.id)
        if current_session.status == 'thinking' or self.turn_manager.is_busy():
            await self.turn_manager._publish_busy_error(session.id)
            raise BusyError('assistant is already responding')

        async with self._ptt_lock:
            if session.id in self._active_ptt:
                raise BusyError('push-to-talk capture is already active')
            self._active_ptt[session.id] = ActivePttLifecycle(
                started_at=datetime.now(timezone.utc).isoformat(),
                device_id=device_id,
                trace_id=trace_id,
            )

        event = EventRecord(session_id=session.id, type='ptt.started', payload={'device_id': device_id, **({'trace_id': trace_id} if trace_id else {})})
        self.db.create_event(event)
        await self.event_bus.publish(self.turn_manager.event_payload(event), session.id)
        self.turn_manager.set_session_state(session.id, None, 'listening', detail='ptt_started')
        await self.turn_manager.publish_state(session.id, None, 'listening', detail='ptt_started')

    async def handle_ptt_release(
        self,
        session: SessionRecord,
        *,
        audio_bytes: bytes,
        filename: str,
        mime_type: str | None,
        device_id: str | None,
        duration_ms: int | None,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        lifecycle = await self._claim_active_lifecycle(session.id)
        effective_trace_id = trace_id or lifecycle.trace_id
        current_session = self._get_current_session(session.id)
        if current_session.status == 'thinking' or self.turn_manager.is_busy():
            await self._restore_session_state_after_rejected_release(session.id, preserve_thinking=True)
            await self.turn_manager._publish_busy_error(session.id)
            raise BusyError('assistant is already responding')

        stopped_event = EventRecord(
            session_id=session.id,
            type='ptt.stopped',
            payload={'device_id': device_id or lifecycle.device_id, 'duration_ms': duration_ms, 'mime_type': mime_type, **({'trace_id': effective_trace_id} if effective_trace_id else {})},
        )
        self.db.create_event(stopped_event)
        await self.event_bus.publish(self.turn_manager.event_payload(stopped_event), session.id)
        self.turn_manager.set_session_state(session.id, None, 'transcribing', detail='ptt_released')
        await self.turn_manager.publish_state(session.id, None, 'transcribing', detail='ptt_released')

        upload_event = EventRecord(session_id=session.id, type='voice.upload.received', payload={'bytes': len(audio_bytes), 'mime_type': mime_type, **({'trace_id': effective_trace_id} if effective_trace_id else {})})
        self.db.create_event(upload_event)
        await self.event_bus.publish(self.turn_manager.event_payload(upload_event), session.id)

        stt_timing = self.db.create_timing(TimingRecord(session_id=session.id, phase='stt', meta={'device_id': device_id or lifecycle.device_id, 'mime_type': mime_type, **({'trace_id': effective_trace_id} if effective_trace_id else {})}))
        started = perf_counter()
        stt_started_event = EventRecord(session_id=session.id, type='stt.started', payload={**({'trace_id': effective_trace_id} if effective_trace_id else {})})
        self.db.create_event(stt_started_event)
        await self.event_bus.publish(self.turn_manager.event_payload(stt_started_event), session.id)
        try:
            result = self.stt.transcribe_bytes(audio_bytes, filename=filename)
        except SttError as exc:
            ended_at = datetime.now(timezone.utc)
            duration = int((perf_counter() - started) * 1000)
            self.db.update_timing(stt_timing.id, ended_at.isoformat(), duration, {'error': str(exc)})
            error_event = EventRecord(session_id=session.id, type='error', payload={'code': 'stt_failed', 'message': str(exc)})
            self.db.create_event(error_event)
            await self.event_bus.publish(self.turn_manager.event_payload(error_event), session.id)
            self.turn_manager.set_session_state(session.id, None, 'error', detail='stt_failed', last_error_at=ended_at.isoformat())
            await self.turn_manager.publish_state(session.id, None, 'error', detail='stt_failed')
            raise VoiceInputError(str(exc)) from exc

        final_text = result.text.strip()
        ended_at = datetime.now(timezone.utc)
        duration = int((perf_counter() - started) * 1000)
        self.db.update_timing(
            stt_timing.id,
            ended_at.isoformat(),
            duration,
            {
                'language': result.language,
                'segment_count': len(result.segments),
                'duration_seconds': result.duration_seconds,
                'transcript_chars': len(final_text),
            },
        )
        final_event = EventRecord(
            session_id=session.id,
            type='stt.final',
            payload={
                'text': final_text,
                'language': result.language,
                'segment_count': len(result.segments),
                'duration_ms': duration,
                **({'trace_id': effective_trace_id} if effective_trace_id else {}),
            },
        )
        self.db.create_event(final_event)
        await self.event_bus.publish(self.turn_manager.event_payload(final_event), session.id)

        if not final_text:
            error_event = EventRecord(session_id=session.id, type='error', payload={'code': 'stt_empty_transcript', 'message': 'Speech-to-text returned no usable transcript.'})
            self.db.create_event(error_event)
            await self.event_bus.publish(self.turn_manager.event_payload(error_event), session.id)
            self.turn_manager.set_session_state(session.id, None, 'error', detail='stt_empty_transcript', last_error_at=ended_at.isoformat())
            await self.turn_manager.publish_state(session.id, None, 'error', detail='stt_empty_transcript')
            raise EmptyTranscriptionError('Speech-to-text returned no usable transcript.')

        try:
            stt_completed_event = EventRecord(session_id=session.id, type='stt.completed', payload={'duration_ms': duration, **({'trace_id': effective_trace_id} if effective_trace_id else {})})
            self.db.create_event(stt_completed_event)
            await self.event_bus.publish(self.turn_manager.event_payload(stt_completed_event), session.id)
            logger.info('stt_latency_summary=%s', {'trace_id': effective_trace_id, 'duration_ms': duration})
            return await self.turn_manager.handle_committed_input(
                session,
                text=final_text,
                source='voice_input',
                modality='voice',
                detail='voice_turn',
                input_metadata={
                    **({'trace_id': effective_trace_id} if effective_trace_id else {}),
                    'input_type': 'voice_turn',
                    'mime_type': mime_type,
                    'device_id': device_id or lifecycle.device_id,
                    'ptt_duration_ms': duration_ms,
                    'stt_language': result.language,
                    'stt_duration_seconds': result.duration_seconds,
                },
                trace_id=effective_trace_id,
            )
        except BusyError:
            await self._restore_session_state_after_rejected_release(session.id, preserve_thinking=True)
            raise

    def _get_current_session(self, session_id: str) -> SessionRecord:
        session = self.db.get_session(session_id)
        if session is None:
            raise InvalidVoiceLifecycleError('session not found')
        return session


    async def cancel_ptt(self, session_id: str) -> None:
        async with self._ptt_lock:
            lifecycle = self._active_ptt.pop(session_id, None)

        if lifecycle is None:
            await self._restore_session_state_after_rejected_release(session_id, preserve_thinking=False)
            return

        await self._restore_session_state_after_rejected_release(session_id, preserve_thinking=False)

    async def _claim_active_lifecycle(self, session_id: str) -> ActivePttLifecycle:
        async with self._ptt_lock:
            lifecycle = self._active_ptt.pop(session_id, None)

        if lifecycle is None:
            await self._restore_session_state_after_rejected_release(session_id, preserve_thinking=False)
            raise InvalidVoiceLifecycleError('push-to-talk release does not match an active capture')

        return lifecycle

    async def _restore_session_state_after_rejected_release(self, session_id: str, *, preserve_thinking: bool) -> None:
        current_session = self.db.get_session(session_id)
        if current_session is None:
            return

        if preserve_thinking and (current_session.status == 'thinking' or self.turn_manager.is_busy()):
            self.turn_manager.set_session_state(session_id, current_session.active_turn_id, 'thinking', detail='assistant_busy')
            await self.turn_manager.publish_state(session_id, current_session.active_turn_id, 'thinking', detail='assistant_busy')
            return

        if current_session.status in {'listening', 'transcribing'}:
            now = datetime.now(timezone.utc).isoformat()
            self.turn_manager.set_session_state(session_id, None, 'ready', detail='ptt_rejected', ready_at=now)
            await self.turn_manager.publish_state(session_id, None, 'ready', detail='ptt_rejected')
