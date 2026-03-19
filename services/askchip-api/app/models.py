from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = 'New chat'
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_message_at: datetime | None = None


class MessageRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    role: Literal['user', 'assistant']
    content: str
    status: Literal['committed', 'streaming', 'completed', 'error'] = 'committed'
    turn_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


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


class CreateSessionRequest(BaseModel):
    title: str | None = None


class RenameSessionRequest(BaseModel):
    title: str


class CreateTurnRequest(BaseModel):
    text: str


class TranscriptResponse(BaseModel):
    session: SessionRecord
    messages: list[MessageRecord]
    events: list[EventRecord]
    timings: list[TimingRecord]


class HealthResponse(BaseModel):
    status: str
    service: str


class ConfigResponse(BaseModel):
    app_name: str
    ollama_base_url: str
    ollama_model: str
    database_path: str
    local_only: bool = True
