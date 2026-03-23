from __future__ import annotations

import re

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
        self._active_playback_by_session: dict[str, str] = {}

    def synthesize_message(self, session_id: str, message_id: str, *, text: str | None = None) -> SynthesizedSpeech:
        message = self._require_message(session_id, message_id, require_completed=text is None)
        speech_text = text if text is not None else message.text
        if not speech_text.strip():
            raise ValueError('assistant speech requires non-empty text')
        started = perf_counter()
        timing = self.db.create_timing(TimingRecord(session_id=session_id, turn_id=message.turn_id, phase='tts', meta={'message_id': message.id, 'chunked': text is not None}))
        try:
            speech = self.tts.synthesize(self._sanitize_tts_text(speech_text))
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
        message = self._require_message(session_id, message_id, require_completed=False)
        active_message_id = self._active_playback_by_session.get(session_id)
        if active_message_id == message_id:
            return
        if active_message_id is not None and active_message_id != message_id:
            raise ValueError('another assistant speech playback is already active for this session')
        self._require_startable_session_state(session_id, message)

        now = datetime.now(timezone.utc).isoformat()
        event = EventRecord(session_id=session_id, turn_id=message.turn_id, type='tts.started', payload={'message_id': message.id})
        self.db.create_event(event)
        self.turn_manager.set_session_state(session_id, message.turn_id, 'speaking', detail='tts_started')
        await self.event_bus.publish(self.turn_manager.event_payload(event), session_id)
        await self.turn_manager.publish_state(session_id, message.turn_id, 'speaking', detail='tts_started')
        self._active_playback_by_session[session_id] = message_id
        self.db.update_message(message.id, text=message.text, status=message.status, updated_at=now, metadata={
            'speech': self._merge_speech_metadata(message, {'last_started_at': now}),
        })

    async def stop_playback(self, session_id: str, message_id: str, *, reason: str) -> str:
        message = self._require_message(session_id, message_id, require_completed=False)
        active_message_id = self._active_playback_by_session.get(session_id)
        if active_message_id != message_id:
            return self._resolve_idle_state(message)

        now = datetime.now(timezone.utc).isoformat()
        next_state = self._resolve_idle_state(message)
        next_detail = 'tts_stopped_waiting_for_more' if next_state == 'thinking' else 'tts_stopped'
        event = EventRecord(session_id=session_id, turn_id=message.turn_id, type='tts.stopped', payload={'message_id': message.id, 'reason': reason})
        self.db.create_event(event)
        self.turn_manager.set_session_state(session_id, message.turn_id if next_state == 'thinking' else None, next_state, detail=next_detail, ready_at=now if next_state == 'ready' else None)
        await self.event_bus.publish(self.turn_manager.event_payload(event), session_id)
        await self.turn_manager.publish_state(session_id, message.turn_id, next_state, detail=next_detail)
        self._active_playback_by_session.pop(session_id, None)
        self.db.update_message(message.id, text=message.text, status=message.status, updated_at=now, metadata={
            'speech': self._merge_speech_metadata(message, {'last_stopped_at': now, 'stop_reason': reason}),
        })
        return next_state

    @staticmethod
    def _sanitize_tts_text(text: str) -> str:
        sanitized = re.sub(r'\s*[\[(\*]\s*(?:laughs?|chuckles?|sighs?|pause|pauses?)\s*[\])\*]\s*', ', ', text, flags=re.IGNORECASE)
        sanitized = re.sub(r'\s*,\s*', ', ', sanitized)
        sanitized = re.sub(r'(?:,\s*){2,}', ', ', sanitized)
        sanitized = re.sub(r'\s+([,.!?;:])', r'\1', sanitized)
        sanitized = re.sub(r'([.!?;,])(?=\S)', r'\1 ', sanitized)
        sanitized = re.sub(r'\s+', ' ', sanitized).strip(' ,')
        return sanitized or text

    def _require_startable_session_state(self, session_id: str, message: MessageRecord) -> None:
        session = self.db.get_session(session_id)
        if session is None:
            raise LookupError('session not found')
        if session.status not in {'ready', 'thinking'}:
            raise ValueError('assistant speech start is stale for the current session state')

        assistant_messages = [
            item for item in self.db.list_messages(session_id)
            if item.role == 'assistant' and item.source == 'model_output' and item.text != ''
        ]
        if not assistant_messages or assistant_messages[-1].id != message.id:
            raise ValueError('assistant speech start is stale for the current session state')

    @staticmethod
    def _merge_speech_metadata(message: MessageRecord, patch: dict[str, object]) -> dict[str, object]:
        existing = message.metadata.get('speech') if isinstance(message.metadata, dict) else None
        if not isinstance(existing, dict):
            existing = {}
        return {**existing, **patch}

    @staticmethod
    def _resolve_idle_state(message: MessageRecord) -> str:
        return 'ready' if message.status == 'completed' else 'thinking'

    def _require_message(self, session_id: str, message_id: str, *, require_completed: bool) -> MessageRecord:
        messages = self.db.list_messages(session_id)
        message = next((item for item in messages if item.id == message_id), None)
        if message is None or message.role != 'assistant' or message.source != 'model_output':
            raise LookupError('assistant message not found')
        if require_completed and (message.status != 'completed' or not message.text):
            raise ValueError('assistant speech requires a completed canonical assistant message')
        if not require_completed and not message.text and message.status == 'completed':
            raise ValueError('assistant speech requires non-empty text')
        return message
