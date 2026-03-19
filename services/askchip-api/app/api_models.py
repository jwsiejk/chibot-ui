from __future__ import annotations

from pydantic import BaseModel

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord


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
