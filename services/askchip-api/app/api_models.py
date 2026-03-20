from __future__ import annotations

from pydantic import BaseModel

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord


class CreateSessionRequest(BaseModel):
    title: str | None = None


class RenameSessionRequest(BaseModel):
    title: str


class CreateTurnRequest(BaseModel):
    text: str


class TranscriptMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    source: str
    modality: str
    status: str
    text: str
    created_at: str
    committed_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, object]

    @classmethod
    def from_record(cls, message: MessageRecord) -> 'TranscriptMessageResponse':
        return cls(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            source=message.source,
            modality=message.modality,
            status=message.status,
            text=message.text,
            created_at=message.created_at.isoformat(),
            committed_at=message.committed_at.isoformat() if message.committed_at else None,
            completed_at=message.completed_at.isoformat() if message.completed_at else None,
            metadata=message.metadata,
        )


class TranscriptResponse(BaseModel):
    session: SessionRecord
    messages: list[TranscriptMessageResponse]
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
    stt_model: str
    stt_device: str
    stt_compute_type: str
    tts_voice: str
    tts_device: str
    tts_model_path: str | None = None
    tts_voices_path: str | None = None
    tts_sample_rate_hz: int
    tts_speed: float
    tts_lang_code: str
    local_only: bool = True
