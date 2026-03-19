from __future__ import annotations

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

    async def begin_ptt(self, session: SessionRecord, *, device_id: str | None) -> None:
        event = EventRecord(session_id=session.id, type='ptt.started', payload={'device_id': device_id})
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
    ) -> dict[str, str]:
        stopped_event = EventRecord(
            session_id=session.id,
            type='ptt.stopped',
            payload={'device_id': device_id, 'duration_ms': duration_ms, 'mime_type': mime_type},
        )
        self.db.create_event(stopped_event)
        await self.event_bus.publish(self.turn_manager.event_payload(stopped_event), session.id)
        self.turn_manager.set_session_state(session.id, None, 'transcribing', detail='ptt_released')
        await self.turn_manager.publish_state(session.id, None, 'transcribing', detail='ptt_released')

        stt_timing = self.db.create_timing(TimingRecord(session_id=session.id, phase='stt', meta={'device_id': device_id, 'mime_type': mime_type}))
        started = perf_counter()
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

        return await self.turn_manager.handle_committed_input(
            session,
            text=final_text,
            source='voice_input',
            modality='voice',
            detail='voice_turn',
            input_metadata={
                'input_type': 'voice_turn',
                'mime_type': mime_type,
                'device_id': device_id,
                'ptt_duration_ms': duration_ms,
                'stt_language': result.language,
                'stt_duration_seconds': result.duration_seconds,
            },
        )
