from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


TurnState = Literal['ready', 'listening', 'transcribing', 'thinking', 'speaking', 'error']
MessageStatus = Literal['pending', 'streaming', 'committed', 'completed', 'error']
MessageSource = Literal['typed_input', 'voice_input', 'model_output', 'system_notice']
MessageModality = Literal['text', 'voice', 'mixed']
PromptRole = Literal['system', 'user', 'assistant']


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = 'New chat'
    status: TurnState = 'ready'
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_message_at: datetime | None = None
    active_turn_id: str | None = None
    ready_at: datetime | None = None
    last_error_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    role: Literal['user', 'assistant']
    text: str
    status: MessageStatus = 'pending'
    turn_id: str
    source: MessageSource
    modality: MessageModality = 'text'
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    committed_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptMessage(BaseModel):
    role: PromptRole
    text: str


class EventRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    turn_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TimingRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    turn_id: str | None = None
    phase: str
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
