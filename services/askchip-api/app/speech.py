from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from app.domain_models import EventRecord, MessageRecord, TimingRecord
from app.events import EventBus
from app.storage import Database
from app.tts import SynthesizedSpeech, TtsAdapter, TtsError
from app.turns import TurnManager


class SpeechService:
    def __init__(self, db: Database, event_bus: EventBus, turn_manager: TurnManager, tts: TtsAdapter) -> None:
        self.db = db
        self.event_bus = event_bus
        self.turn_manager = turn_manager
        self.tts = tts

    def synthesize_message(self, session_id: str, message_id: str) -> SynthesizedSpeech:
        message = self._require_message(session_id, message_id)
        started = perf_counter()
        timing = self.db.create_timing(TimingRecord(session_id=session_id, turn_id=message.turn_id, phase='tts', meta={'message_id': message.id}))
        try:
            speech = self.tts.synthesize(message.text)
        except TtsError as exc:
            ended_at = datetime.now(timezone.utc)
            self.db.update_timing(timing.id, ended_at.isoformat(), int((perf_counter() - started) * 1000), {'error': str(exc), 'message_id': message.id})
            raise

        ended_at = datetime.now(timezone.utc)
        self.db.update_timing(
            timing.id,
            ended_at.isoformat(),
            int((perf_counter() - started) * 1000),
            {
                'message_id': message.id,
                'sample_rate_hz': speech.sample_rate_hz,
                'duration_ms': speech.duration_ms,
                **speech.metadata,
            },
        )
        return speech

    async def start_playback(self, session_id: str, message_id: str) -> None:
        message = self._require_message(session_id, message_id)
        now = datetime.now(timezone.utc).isoformat()
        event = EventRecord(session_id=session_id, turn_id=message.turn_id, type='tts.started', payload={'message_id': message.id})
        self.db.create_event(event)
        self.turn_manager.set_session_state(session_id, message.turn_id, 'speaking', detail='tts_started')
        await self.event_bus.publish(self.turn_manager.event_payload(event), session_id)
        await self.turn_manager.publish_state(session_id, message.turn_id, 'speaking', detail='tts_started')
        self.db.update_message(message.id, text=message.text, status=message.status, updated_at=now, metadata={'speech': {'last_started_at': now}})

    async def stop_playback(self, session_id: str, message_id: str, *, reason: str) -> None:
        message = self._require_message(session_id, message_id)
        now = datetime.now(timezone.utc).isoformat()
        event = EventRecord(session_id=session_id, turn_id=message.turn_id, type='tts.stopped', payload={'message_id': message.id, 'reason': reason})
        self.db.create_event(event)
        self.turn_manager.set_session_state(session_id, None, 'ready', detail='tts_stopped', ready_at=now)
        await self.event_bus.publish(self.turn_manager.event_payload(event), session_id)
        await self.turn_manager.publish_state(session_id, message.turn_id, 'ready', detail='tts_stopped')
        self.db.update_message(message.id, text=message.text, status=message.status, updated_at=now, metadata={'speech': {'last_stopped_at': now, 'stop_reason': reason}})

    def _require_message(self, session_id: str, message_id: str) -> MessageRecord:
        messages = self.db.list_messages(session_id)
        message = next((item for item in messages if item.id == message_id), None)
        if message is None or message.role != 'assistant' or message.source != 'model_output':
            raise LookupError('assistant message not found')
        if message.status != 'completed' or not message.text:
            raise ValueError('assistant speech requires a completed canonical assistant message')
        return message
