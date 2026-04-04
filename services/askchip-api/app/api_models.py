from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain_models import EventRecord, MessageRecord, SessionRecord, TimingRecord


class VmwareTriageState(BaseModel):
    issue_family: str = ''
    suspected_layer: str = ''
    impact_scope: str = ''
    recent_change_summary: str = ''
    symptom_summary: str = ''
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    conversation_stage: str = ''
    policy_next_move: str = ''
    next_best_question: str = ''
    required_logs: list[str] = Field(default_factory=list)
    received_logs: list[str] = Field(default_factory=list)
    missing_logs: list[str] = Field(default_factory=list)
    log_sufficiency_status: str = ''
    optional_logs: list[str] = Field(default_factory=list)
    log_guidance_summary: str = ''
    resolution_status: str = ''
    last_updated_from_turn_id: str = ''


class VmwareHandoffPacket(BaseModel):
    issue_summary: str = ''
    working_hypothesis: str = ''
    confirmed_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    logs_received: list[str] = Field(default_factory=list)
    logs_missing: list[str] = Field(default_factory=list)
    log_sufficiency_status: str = ''
    current_resolution_status: str = ''
    recommended_next_step: str = ''
    handoff_reason: str = ''
    ready_for_handoff: bool = False




class VmwareArtifactEvidence(BaseModel):
    parser_kind: str
    artifact_type: str
    parsed_line_count: int = 0
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    matched_categories: list[str] = Field(default_factory=list)
    notable_lines: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


class VmwareArtifactRecord(BaseModel):
    id: str
    session_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: str
    artifact_type: str
    uploaded_at: str
    storage_path: str
    parse_error: str | None = None
    evidence: VmwareArtifactEvidence | None = None


class UploadSessionArtifactResponse(BaseModel):
    artifact: VmwareArtifactRecord


class SessionArtifactsResponse(BaseModel):
    items: list[VmwareArtifactRecord] = Field(default_factory=list)

class ExpertDeskSessionMetadata(BaseModel):
    request_label: str
    issue_category: str
    environment_platform: str
    urgency: str
    preferred_expert_type: str
    recommended_expert_type: str
    recommended_path: str
    expert_persona_id: str = ''
    expert_persona_label: str = ''
    expert_persona_summary: str = ''
    expert_persona: str = ''
    issue_description: str
    architecture_notes: str
    error_text: str
    uploaded_logs_count: int = 0
    uploaded_log_names: list[str] = Field(default_factory=list)
    uploaded_logs_available: bool = False
    recommended_vmware_logs: list[str] = Field(default_factory=list)
    vmware_artifacts: list[VmwareArtifactRecord] = Field(default_factory=list)
    vmware_triage: VmwareTriageState | None = None
    vmware_handoff: VmwareHandoffPacket | None = None


class CreateSessionMetadata(BaseModel):
    expert_desk: ExpertDeskSessionMetadata | None = None


class CreateSessionRequest(BaseModel):
    title: str | None = None
    metadata: CreateSessionMetadata | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    metadata: CreateSessionMetadata | None = None


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


class ReadinessCheck(BaseModel):
    label: str
    status: str
    detail: str | None = None
    checked_at: str | None = None
    optional: bool = False


class ReadinessResponse(BaseModel):
    local_only: bool = True
    ollama_warmup_enabled: bool = True
    tts_warmup_enabled: bool = False
    warmup_active: bool
    runtime: dict[str, object] | None = None
    checks: dict[str, ReadinessCheck]


class ConfigResponse(BaseModel):
    app_name: str
    ollama_base_url: str
    ollama_model: str
    ollama_keep_alive: str
    ollama_num_ctx: int
    ollama_num_parallel: int
    database_path: str
    stt_model: str
    stt_requested_device: str
    stt_device: str
    stt_requested_compute_type: str
    stt_compute_type: str
    tts_voice: str
    tts_requested_device: str
    tts_device: str
    tts_provider: str
    tts_available_providers: list[str] = Field(default_factory=list)
    tts_warning: str | None = None
    tts_fallback_reason: str | None = None
    tts_model_path: str | None = None
    tts_voices_path: str | None = None
    tts_sample_rate_hz: int
    tts_speed: float
    tts_lang_code: str
    local_only: bool = True
    ollama_warmup_enabled: bool = True
    tts_warmup_enabled: bool = False
