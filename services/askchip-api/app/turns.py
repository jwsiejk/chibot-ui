from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord
from app.events import EventBus
from app.ollama import OllamaClient, OllamaUnavailableError
from app.prompting import PromptAssembler
from app.storage import Database, DatabaseError


class BusyError(RuntimeError):
    pass


class TurnManager:
    def __init__(self, db: Database, event_bus: EventBus, ollama: OllamaClient, prompt_assembler: PromptAssembler) -> None:
        self.db = db
        self.event_bus = event_bus
        self.ollama = ollama
        self.prompt_assembler = prompt_assembler
        self._job_lock = asyncio.Lock()

    async def handle_typed_turn(self, session: SessionRecord, text: str) -> dict[str, str]:
        return await self.handle_committed_input(
            session,
            text=text,
            source='typed_input',
            modality='text',
            detail='typed_turn',
            input_metadata={'input_type': 'typed_turn'},
        )

    async def handle_committed_input(
        self,
        session: SessionRecord,
        *,
        text: str,
        source: str,
        modality: str,
        detail: str,
        input_metadata: dict[str, object],
    ) -> dict[str, str]:
        if self._job_lock.locked():
            await self._publish_busy_error(session.id)
            raise BusyError('assistant is already responding')

        async with self._job_lock:
            turn_id = str(uuid4())
            turn_started_at = datetime.now(timezone.utc)
            started = perf_counter()
            turn_timing = self.db.create_timing(TimingRecord(session_id=session.id, turn_id=turn_id, phase='turn', meta={'state': 'thinking', 'source': source, 'modality': modality}))
            model_timing = self.db.create_timing(TimingRecord(session_id=session.id, turn_id=turn_id, phase='model_stream', meta={'source': source, 'modality': modality}))
            self.set_session_state(session.id, turn_id, 'thinking', detail=detail)
            await self.publish_state(session.id, turn_id, 'thinking', detail=detail)

            user_message = MessageRecord(
                session_id=session.id,
                role='user',
                text=text,
                status='committed',
                turn_id=turn_id,
                source=source,
                modality=modality,
                committed_at=turn_started_at,
                metadata=input_metadata,
            )
            self.db.create_message(user_message)
            committed_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='turn.committed',
                payload={'message_id': user_message.id, 'text': text, 'source': source, 'modality': modality},
            )
            self.db.create_event(committed_event)
            await self.event_bus.publish(self.event_payload(committed_event), session.id)

            assistant_message = MessageRecord(
                session_id=session.id,
                role='assistant',
                text='',
                status='streaming',
                turn_id=turn_id,
                source='model_output',
                modality='text',
                metadata={'model': self.ollama.model},
            )
            self.db.create_message(assistant_message)

            transcript = self.db.list_messages(session.id)
            prompt_messages = self.prompt_assembler.build_messages(transcript=transcript, user_text=text)
            prompt_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='prompt.assembled',
                payload={'message_count': len(prompt_messages), 'transcript_window': self.prompt_assembler.transcript_window},
            )
            self.db.create_event(prompt_event)
            await self.event_bus.publish(self.event_payload(prompt_event), session.id)

            started_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='assistant.started',
                payload={'message_id': assistant_message.id, 'model': self.ollama.model},
            )
            self.db.create_event(started_event)
            await self.event_bus.publish(self.event_payload(started_event), session.id)

            first_chunk_ms: int | None = None
            assembled: list[str] = []
            provider_metrics: dict[str, object] = {}
            try:
                async for chunk in self.ollama.stream_chat(prompt_messages):
                    provider_metrics.update(chunk.get('metrics', {}))
                    delta_text = str(chunk.get('text', ''))
                    if delta_text:
                        if first_chunk_ms is None:
                            first_chunk_ms = int((perf_counter() - started) * 1000)
                        assembled.append(delta_text)
                        delta_event = EventRecord(session_id=session.id, turn_id=turn_id, type='assistant.delta', payload={'message_id': assistant_message.id, 'delta': delta_text})
                        self.db.create_event(delta_event)
                        await self.event_bus.publish(self.event_payload(delta_event), session.id)
                completed_text = ''.join(assembled)
                ended_at = datetime.now(timezone.utc)
                self.db.update_message(
                    assistant_message.id,
                    text=completed_text,
                    status='completed',
                    updated_at=ended_at.isoformat(),
                    completed_at=ended_at.isoformat(),
                    metadata={'first_chunk_ms': first_chunk_ms, 'provider_metrics': provider_metrics},
                )
                completed_event = EventRecord(session_id=session.id, turn_id=turn_id, type='assistant.completed', payload={'message_id': assistant_message.id, 'text': completed_text, 'first_chunk_ms': first_chunk_ms})
                self.db.create_event(completed_event)
                await self.event_bus.publish(self.event_payload(completed_event), session.id)
                self.set_session_state(session.id, None, 'ready', ready_at=ended_at.isoformat(), detail='turn_complete')
                await self.publish_state(session.id, turn_id, 'ready', detail='turn_complete')
                duration_ms = int((perf_counter() - started) * 1000)
                self.db.update_timing(model_timing.id, ended_at.isoformat(), duration_ms, {'first_chunk_ms': first_chunk_ms, **provider_metrics})
                self.db.update_timing(turn_timing.id, ended_at.isoformat(), duration_ms, {'assistant_message_id': assistant_message.id, 'first_chunk_ms': first_chunk_ms})
                return {'turn_id': turn_id, 'assistant_message_id': assistant_message.id}
            except (OllamaUnavailableError, DatabaseError) as exc:
                ended_at = datetime.now(timezone.utc)
                self.db.update_message(
                    assistant_message.id,
                    text=''.join(assembled),
                    status='error',
                    updated_at=ended_at.isoformat(),
                    metadata={'provider_metrics': provider_metrics},
                )
                error_event = EventRecord(session_id=session.id, turn_id=turn_id, type='error', payload={'code': 'ollama_unavailable', 'message': str(exc)})
                self.db.create_event(error_event)
                await self.event_bus.publish(self.event_payload(error_event), session.id)
                self.set_session_state(session.id, None, 'error', last_error_at=ended_at.isoformat(), detail='ollama_unavailable')
                await self.publish_state(session.id, turn_id, 'error', detail='ollama_unavailable')
                duration_ms = int((perf_counter() - started) * 1000)
                self.db.update_timing(model_timing.id, ended_at.isoformat(), duration_ms, {'error': str(exc), **provider_metrics})
                self.db.update_timing(turn_timing.id, ended_at.isoformat(), duration_ms, {'error': str(exc)})
                raise

    async def _publish_busy_error(self, session_id: str) -> None:
        event = EventRecord(session_id=session_id, type='error', payload={'code': 'assistant_busy', 'message': 'Assistant is already responding.'})
        self.db.create_event(event)
        await self.event_bus.publish(self.event_payload(event), session_id)

    def set_session_state(
        self,
        session_id: str,
        active_turn_id: str | None,
        state: str,
        *,
        detail: str,
        ready_at: str | None = None,
        last_error_at: str | None = None,
    ) -> None:
        self.db.update_session_state(
            session_id,
            status=state,
            updated_at=datetime.now(timezone.utc).isoformat(),
            active_turn_id=active_turn_id,
            ready_at=ready_at,
            last_error_at=last_error_at,
            metadata={'detail': detail},
        )

    async def publish_state(self, session_id: str, turn_id: str | None, state: str, *, detail: str) -> None:
        event = EventRecord(session_id=session_id, turn_id=turn_id, type='state', payload={'state': state, 'active_turn_id': turn_id if state == 'thinking' else None, 'detail': detail})
        self.db.create_event(event)
        await self.event_bus.publish(self.event_payload(event), session_id)

    @staticmethod
    def event_payload(event: EventRecord) -> dict[str, object]:
        return {
            'id': event.id,
            'session_id': event.session_id,
            'turn_id': event.turn_id,
            'type': event.type,
            'payload': event.payload,
            'created_at': event.created_at.isoformat(),
        }
