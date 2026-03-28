from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord
from app.events import EventBus
from app.ollama import OllamaClient, OllamaUnavailableError
from app.prompting import PromptAssembler
from app.reasoning import route_reasoning
from app.storage import Database, DatabaseError


class BusyError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


class TurnManager:
    def __init__(self, db: Database, event_bus: EventBus, ollama: OllamaClient, prompt_assembler: PromptAssembler) -> None:
        self.db = db
        self.event_bus = event_bus
        self.ollama = ollama
        self.prompt_assembler = prompt_assembler
        self._job_lock = asyncio.Lock()

    async def handle_typed_turn(self, session: SessionRecord, text: str, *, trace_id: str | None = None) -> dict[str, str]:
        return await self.handle_committed_input(
            session,
            text=text,
            source='typed_input',
            modality='text',
            detail='typed_turn',
            input_metadata={'input_type': 'typed_turn', **({'trace_id': trace_id} if trace_id else {})},
            trace_id=trace_id,
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
        trace_id: str | None = None,
    ) -> dict[str, str]:
        if self._job_lock.locked():
            await self._publish_busy_error(session.id)
            raise BusyError('assistant is already responding')

        async with self._job_lock:
            turn_id = str(uuid4())
            turn_started_at = datetime.now(timezone.utc)
            started = perf_counter()
            reasoning = route_reasoning(text)
            user_text = reasoning.user_text or text.strip()
            turn_timing = self.db.create_timing(TimingRecord(session_id=session.id, turn_id=turn_id, phase='turn', meta={'state': 'thinking', 'source': source, 'modality': modality, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, **({'trace_id': trace_id} if trace_id else {})}))
            model_timing = self.db.create_timing(TimingRecord(session_id=session.id, turn_id=turn_id, phase='model_stream', meta={'source': source, 'modality': modality, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, **({'trace_id': trace_id} if trace_id else {})}))
            self.set_session_state(session.id, turn_id, 'thinking', detail=detail)
            await self.publish_state(session.id, turn_id, 'thinking', detail=detail)

            user_message = MessageRecord(
                session_id=session.id,
                role='user',
                text=user_text,
                status='committed',
                turn_id=turn_id,
                source=source,
                modality=modality,
                committed_at=turn_started_at,
                metadata={**input_metadata, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think},
            )
            self.db.create_message(user_message)
            committed_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='turn.committed',
                payload={'message_id': user_message.id, 'text': user_text, 'source': source, 'modality': modality, **({'trace_id': trace_id} if trace_id else {})},
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
                metadata={'model': self.ollama.model, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, **({'trace_id': trace_id} if trace_id else {})},
            )
            self.db.create_message(assistant_message)

            transcript = self.db.list_messages(session.id)
            prompt_messages = self.prompt_assembler.build_messages(transcript=transcript, user_text=user_text)
            prompt_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='prompt.assembled',
                payload={'message_count': len(prompt_messages), 'transcript_window': self.prompt_assembler.transcript_window},
            )
            self.db.create_event(prompt_event)
            await self.event_bus.publish(self.event_payload(prompt_event), session.id)

            reasoning_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='reasoning.selected',
                payload={'mode': reasoning.mode, 'think': reasoning.think, **({'trace_id': trace_id} if trace_id else {})},
            )
            self.db.create_event(reasoning_event)
            await self.event_bus.publish(self.event_payload(reasoning_event), session.id)

            started_event = EventRecord(
                session_id=session.id,
                turn_id=turn_id,
                type='assistant.started',
                payload={'message_id': assistant_message.id, 'model': self.ollama.model, 'reasoning_mode': reasoning.mode, 'think': reasoning.think, **({'trace_id': trace_id} if trace_id else {})},
            )
            self.db.create_event(started_event)
            await self.event_bus.publish(self.event_payload(started_event), session.id)

            first_chunk_ms: int | None = None
            assembled: list[str] = []
            provider_metrics: dict[str, object] = {}
            thinking_present = False
            try:
                async for chunk in self.ollama.stream_chat(prompt_messages, think=reasoning.think):
                    provider_metrics.update(chunk.get('metrics', {}))
                    thinking_present = thinking_present or bool(chunk.get('thinking_present', False))
                    delta_text = str(chunk.get('text', ''))
                    if delta_text:
                        if first_chunk_ms is None:
                            first_chunk_ms = int((perf_counter() - started) * 1000)
                            first_chunk_event = EventRecord(session_id=session.id, turn_id=turn_id, type='assistant.first_chunk', payload={'message_id': assistant_message.id, 'latency_ms': first_chunk_ms, **({'trace_id': trace_id} if trace_id else {})})
                            self.db.create_event(first_chunk_event)
                            await self.event_bus.publish(self.event_payload(first_chunk_event), session.id)
                        assembled.append(delta_text)
                        delta_event = EventRecord(session_id=session.id, turn_id=turn_id, type='assistant.delta', payload={'message_id': assistant_message.id, 'delta': delta_text, **({'trace_id': trace_id} if trace_id else {})})
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
                    metadata={'first_chunk_ms': first_chunk_ms, 'provider_metrics': provider_metrics, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, 'thinking_present': thinking_present},
                )
                completed_event = EventRecord(session_id=session.id, turn_id=turn_id, type='assistant.completed', payload={'message_id': assistant_message.id, 'text': completed_text, 'first_chunk_ms': first_chunk_ms, **({'trace_id': trace_id} if trace_id else {})})
                self.db.create_event(completed_event)
                await self.event_bus.publish(self.event_payload(completed_event), session.id)
                self.set_session_state(session.id, None, 'ready', ready_at=ended_at.isoformat(), detail='turn_complete')
                await self.publish_state(session.id, turn_id, 'ready', detail='turn_complete')
                duration_ms = int((perf_counter() - started) * 1000)
                self.db.update_timing(model_timing.id, ended_at.isoformat(), duration_ms, {'first_chunk_ms': first_chunk_ms, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, 'thinking_present': thinking_present, **provider_metrics})
                self.db.update_timing(turn_timing.id, ended_at.isoformat(), duration_ms, {'assistant_message_id': assistant_message.id, 'first_chunk_ms': first_chunk_ms, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, 'thinking_present': thinking_present})
                summary_payload = {'trace_id': trace_id, 'turn_id': turn_id, 'source': source, 'total_turn_ms': duration_ms, 'model_first_chunk_ms': first_chunk_ms, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think, 'thinking_present': thinking_present}
                summary_event = EventRecord(session_id=session.id, turn_id=turn_id, type='turn.latency', payload={k: v for k, v in summary_payload.items() if v is not None})
                self.db.create_event(summary_event)
                await self.event_bus.publish(self.event_payload(summary_event), session.id)
                logger.info('turn_latency_summary=%s', summary_event.payload)
                return {'turn_id': turn_id, 'assistant_message_id': assistant_message.id}
            except (OllamaUnavailableError, DatabaseError) as exc:
                ended_at = datetime.now(timezone.utc)
                self.db.update_message(
                    assistant_message.id,
                    text=''.join(assembled),
                    status='error',
                    updated_at=ended_at.isoformat(),
                    metadata={'provider_metrics': provider_metrics, 'reasoning_mode': reasoning.mode, 'thinking_used': reasoning.think},
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


    def is_busy(self) -> bool:
        return self._job_lock.locked()
