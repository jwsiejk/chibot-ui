export type TurnState = 'ready' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error';
export type MessageRole = 'user' | 'assistant';
export type MessageSource = 'typed_input' | 'voice_input' | 'model_output' | 'system_notice';
export type MessageModality = 'text' | 'voice' | 'mixed';
export type MessageStatus = 'pending' | 'streaming' | 'committed' | 'completed' | 'error';

export interface SessionRecord {
  id: string;
  title: string;
  status: TurnState;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  active_turn_id: string | null;
  ready_at: string | null;
  last_error_at: string | null;
  metadata: Record<string, unknown>;
}

export interface TranscriptMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  source: MessageSource;
  modality: MessageModality;
  status: MessageStatus;
  text: string;
  created_at: string;
  committed_at: string | null;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

export interface EventRecord {
  id: string;
  session_id: string | null;
  turn_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TimingRecord {
  id: string;
  session_id: string | null;
  turn_id: string | null;
  phase: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  meta: Record<string, unknown>;
}

export interface TranscriptResponse {
  session: SessionRecord;
  messages: TranscriptMessage[];
  events: EventRecord[];
  timings: TimingRecord[];
}

export interface SessionsResponse {
  items: SessionRecord[];
}

export interface ConfigResponse {
  app_name: string;
  ollama_base_url: string;
  ollama_model: string;
  database_path: string;
  stt_model: string;
  stt_device: string;
  stt_compute_type: string;
  tts_voice: string;
  tts_requested_device: string;
  tts_device: string;
  tts_provider: string;
  tts_available_providers: string[];
  tts_warning: string | null;
  tts_fallback_reason: string | null;
  tts_model_path: string | null;
  tts_voices_path: string | null;
  tts_sample_rate_hz: number;
  tts_speed: number;
  tts_lang_code: string;
  local_only: boolean;
  ollama_warmup_enabled: boolean;
  tts_warmup_enabled: boolean;
}

export interface ReadinessCheck {
  label: string;
  status: string;
  detail: string | null;
  checked_at: string | null;
  optional: boolean;
}

export interface ReadinessResponse {
  local_only: boolean;
  warmup_active: boolean;
  checks: Record<string, ReadinessCheck>;
}

export interface CreateSessionRequest {
  title?: string;
  metadata?: CreateSessionMetadata;
}


export interface VmwareTriageState {
  issue_family: string;
  suspected_layer: string;
  impact_scope: string;
  recent_change_summary: string;
  symptom_summary: string;
  open_questions: string[];
  confidence: number;
  conversation_stage: string;
  next_best_question: string;
  required_logs: string[];
  received_logs: string[];
  missing_logs: string[];
  log_sufficiency_status: string;
  optional_logs: string[];
  log_guidance_summary: string;
  resolution_status: string;
  last_updated_from_turn_id: string;
}

export interface VmwareHandoffPacket {
  issue_summary: string;
  working_hypothesis: string;
  confirmed_facts: string[];
  open_questions: string[];
  actions_taken: string[];
  logs_received: string[];
  logs_missing: string[];
  log_sufficiency_status: string;
  current_resolution_status: string;
  recommended_next_step: string;
  handoff_reason: string;
  ready_for_handoff: boolean;
}

export interface VmwareArtifactEvidence {
  parser_kind: string;
  artifact_type: string;
  parsed_line_count: number;
  timestamp_start: string | null;
  timestamp_end: string | null;
  matched_categories: string[];
  notable_lines: string[];
  parse_warnings: string[];
}

export interface VmwareArtifactRecord {
  id: string;
  session_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: 'metadata_only' | 'uploaded_supported_unparsed' | 'parsed_supported' | 'uploaded_unsupported' | 'parse_failed';
  artifact_type: string;
  uploaded_at: string;
  storage_path: string;
  parse_error?: string | null;
  evidence?: VmwareArtifactEvidence | null;
}

export interface ExpertDeskSessionMetadata {
  request_label: string;
  issue_category: string;
  environment_platform: string;
  urgency: string;
  preferred_expert_type: string;
  recommended_expert_type: string;
  recommended_path: string;
  expert_persona_id: string;
  expert_persona_label: string;
  expert_persona_summary: string;
  issue_description: string;
  architecture_notes: string;
  error_text: string;
  uploaded_logs_count: number;
  uploaded_log_names: string[];
  uploaded_logs_available: boolean;
  recommended_vmware_logs?: string[];
  vmware_artifacts?: VmwareArtifactRecord[];
  vmware_triage?: VmwareTriageState;
  vmware_handoff?: VmwareHandoffPacket;
}

export interface CreateSessionMetadata {
  expert_desk?: ExpertDeskSessionMetadata;
}

export interface CreateTurnRequest {
  text: string;
}

export interface UpdateSessionRequest {
  title?: string;
  metadata?: {
    expert_desk?: Partial<ExpertDeskSessionMetadata> & Record<string, unknown>;
  };
}

export interface CreateTurnResponse {
  status: 'completed';
  turn_id: string;
  assistant_message_id: string;
}

export interface AskChipEvent {
  id: string;
  session_id: string | null;
  turn_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}
