import type { AskChappyMetadata } from '../../../../shared/contracts/session';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
export type AssistantRuntimeRequest = { session_id: string; metadata: AskChappyMetadata; transcript: TranscriptMessage[]; latest_user_text: string; };
export type AssistantRuntimeProviderMetadata = { provider: 'ollama_local'; model: string; base_url: string; };
export type AssistantRuntimeSuccess = { ok: true; text: string; runtime: AssistantRuntimeProviderMetadata; };
export type AssistantRuntimeErrorCode = 'runtime_unavailable' | 'model_unavailable' | 'invalid_response';
export type AssistantRuntimeError = { ok: false; code: AssistantRuntimeErrorCode; message: string; runtime: AssistantRuntimeProviderMetadata; };
export type AssistantRuntimeResult = AssistantRuntimeSuccess | AssistantRuntimeError;
