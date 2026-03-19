from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from app.db import Database, DatabaseError
from app.events import EventBus
from app.models import EventRecord, MessageRecord, SessionRecord, TimingRecord
from app.ollama import OllamaClient, OllamaUnavailableError


class BusyError(RuntimeError):
    pass


class TurnManager:
    def __init__(self, db: Database, event_bus: EventBus, ollama: OllamaClient) -> None:
        self.db = db
        self.event_bus = event_bus
        self.ollama = ollama
        self._job_lock = asyncio.Lock()

    async def handle_typed_turn(self, session: SessionRecord, text: str) -> dict[str, str]:
        if self._job_lock.locked():
            event = EventRecord(session_id=session.id, type='error', payload={'code': 'assistant_busy', 'message': 'Assistant is already responding.'})
            self.db.create_event(event)
            await self.event_bus.publish(self._event_payload(event), session.id)
            raise BusyError('assistant is already responding')

        async with self._job_lock:
            turn_id = str(uuid4())
            started = perf_counter()
            timing = TimingRecord(session_id=session.id, turn_id=turn_id, phase='turn')
            self.db.create_timing(timing)

            user_message = MessageRecord(session_id=session.id, role='user', content=text, status='committed', turn_id=turn_id)
            self.db.create_message(user_message)
            committed_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='turn.committed',
                payload={'message_id': user_message.id, 'text': text},
            )
            self.db.create_event(committed_event)
            await self.event_bus.publish(self._event_payload(committed_event), session.id)

            assistant_message = MessageRecord(session_id=session.id, role='assistant', content='', status='streaming', turn_id=turn_id)
            self.db.create_message(assistant_message)

            loading_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='state',
                payload={'state': 'model_loading', 'model': self.ollama.model},
            )
            self.db.create_event(loading_event)
            await self.event_bus.publish(self._event_payload(loading_event), session.id)

            transcript = self.db.list_messages(session.id)
            history = [{'role': message.role, 'content': message.content} for message in transcript if message.role == 'user' or message.content]
            assembled: list[str] = []
            started_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='assistant.started',
                payload={'message_id': assistant_message.id, 'model': self.ollama.model},
            )
            self.db.create_event(started_event)
            await self.event_bus.publish(self._event_payload(started_event), session.id)
            try:
                async for chunk in self.ollama.stream_chat(history):
                    content = str(chunk.get('content', ''))
                    if content:
                        assembled.append(content)
                        delta_event = EventRecord(
                            session_id=session.id,
                            turn_id=turn_id,
                            type='assistant.delta',
                            payload={'message_id': assistant_message.id, 'delta': content},
                        )
                        self.db.create_event(delta_event)
                        await self.event_bus.publish(self._event_payload(delta_event), session.id)
                completed_text = ''.join(assembled)
                ended_at = datetime.now(timezone.utc)
                self.db.update_message_content(assistant_message.id, completed_text, 'completed', ended_at.isoformat())
                completed_event = EventRecord(
                    session_id=session.id,
                    turn_id=turn_id,
                    type='assistant.completed',
                    payload={'message_id': assistant_message.id, 'text': completed_text},
                )
                self.db.create_event(completed_event)
                await self.event_bus.publish(self._event_payload(completed_event), session.id)
                state_event = EventRecord(
                    session_id=session.id,
                    turn_id=turn_id,
                    type='state',
                    payload={'state': 'idle', 'active_turn_id': None},
                )
                self.db.create_event(state_event)
                await self.event_bus.publish(self._event_payload(state_event), session.id)
                duration_ms = int((perf_counter() - started) * 1000)
                self.db.update_timing(timing.id, ended_at.isoformat(), duration_ms, {'message_id': assistant_message.id})
                return {'turn_id': turn_id, 'assistant_message_id': assistant_message.id}
            except (OllamaUnavailableError, DatabaseError) as exc:
                ended_at = datetime.now(timezone.utc)
                self.db.update_message_content(assistant_message.id, ''.join(assembled), 'error', ended_at.isoformat())
                error_event = EventRecord(
                    session_id=session.id,
                    turn_id=turn_id,
                    type='error',
                    payload={'code': 'ollama_unavailable', 'message': str(exc)},
                )
                self.db.create_event(error_event)
                await self.event_bus.publish(self._event_payload(error_event), session.id)
                duration_ms = int((perf_counter() - started) * 1000)
                self.db.update_timing(timing.id, ended_at.isoformat(), duration_ms, {'error': str(exc)})
                raise

    @staticmethod
    def _event_payload(event: EventRecord) -> dict[str, object]:
        return {
            'id': event.id,
            'session_id': event.session_id,
            'turn_id': event.turn_id,
            'type': event.type,
            'payload': event.payload,
            'created_at': event.created_at.isoformat(),
        }
