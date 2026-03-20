export type TurnState = 'ready' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error';
export type MessageRole = 'user' | 'assistant';
export type MessageSource = 'typed_input' | 'voice_input' | 'model_output' | 'system_notice';
export type MessageModality = 'text' | 'voice' | 'mixed';
export type MessageStatus = 'pending' | 'streaming' | 'committed' | 'completed' | 'interrupted' | 'error';

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
  tts_device: string;
  tts_model_path: string | null;
  tts_voices_path: string | null;
  tts_sample_rate_hz: number;
  tts_speed: number;
  tts_lang_code: string;
  local_only: boolean;
}

export interface CreateSessionRequest {
  title?: string;
}

export interface CreateTurnRequest {
  text: string;
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
