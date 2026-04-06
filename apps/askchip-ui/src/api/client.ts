import type {
  ConfigResponse,
  ReadinessResponse,
  CreateSessionRequest,
  UpdateSessionRequest,
  CreateTurnRequest,
  CreateTurnResponse,
  VmwareArtifactRecord,
  SessionRecord,
  SessionsResponse,
  TranscriptResponse,
} from '../types/contract';
import { runtimeConfig } from '../config/runtime';

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export class ApiTimeoutError extends ApiError {
  constructor(detail: string) {
    super(408, detail);
    this.name = 'ApiTimeoutError';
  }
}

const DEFAULT_BOOTSTRAP_TIMEOUT_MS = 8000;

export class AskChipApiClient {
  readonly baseUrl: string;

  constructor(baseUrl = runtimeConfig.apiBaseUrl) {
    this.baseUrl = baseUrl;
  }

  async getConfig(): Promise<ConfigResponse> {
    return this.request<ConfigResponse>('/api/v1/config', {
      timeoutMs: DEFAULT_BOOTSTRAP_TIMEOUT_MS,
      timeoutMessage: 'Config request timed out while loading AskChip.',
    });
  }

  async getReadiness(): Promise<ReadinessResponse> {
    return this.request<ReadinessResponse>('/api/v1/readiness', {
      timeoutMs: DEFAULT_BOOTSTRAP_TIMEOUT_MS,
      timeoutMessage: 'Readiness request timed out while loading AskChip.',
    });
  }

  async listSessions(): Promise<SessionRecord[]> {
    const response = await this.request<SessionsResponse>('/api/v1/sessions', {
      timeoutMs: DEFAULT_BOOTSTRAP_TIMEOUT_MS,
      timeoutMessage: 'Sessions request timed out while loading AskChip.',
    });
    return response.items;
  }

  async createSession(payload: CreateSessionRequest): Promise<SessionRecord> {
    return this.request<SessionRecord>('/api/v1/sessions', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getTranscript(sessionId: string): Promise<TranscriptResponse> {
    return this.request<TranscriptResponse>(`/api/v1/sessions/${sessionId}/transcript`, {
      timeoutMs: DEFAULT_BOOTSTRAP_TIMEOUT_MS,
      timeoutMessage: 'Transcript request timed out while loading the selected session.',
    });
  }

  async deleteSession(sessionId: string): Promise<{ status: string; session_id: string; }> {
    return this.request<{ status: string; session_id: string; }>(`/api/v1/sessions/${sessionId}`, {
      method: 'DELETE',
    });
  }

  async updateSession(sessionId: string, payload: UpdateSessionRequest): Promise<SessionRecord> {
    return this.request<SessionRecord>(`/api/v1/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async createTurn(sessionId: string, payload: CreateTurnRequest, traceId?: string): Promise<CreateTurnResponse> {
    return this.request<CreateTurnResponse>(`/api/v1/sessions/${sessionId}/turns`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: traceId ? { 'X-AskChip-Trace-Id': traceId } : undefined,
    });
  }

  async getAssistantSpeech(sessionId: string, messageId: string, text?: string, traceId?: string): Promise<{ audio: HTMLAudioElement; objectUrl: string; fetchStartedAt: number; fetchEndedAt: number; }> {
    const search = text ? `?text=${encodeURIComponent(text)}` : '';
    const fetchStartedAt = Date.now();
    const response = await fetch(`${this.baseUrl}/api/v1/sessions/${sessionId}/messages/${messageId}/speech${search}`, {
      headers: traceId ? { 'X-AskChip-Trace-Id': traceId } : undefined,
    });
    if (!response.ok) {
      throw new ApiError(response.status, await this.getErrorDetail(response, `Assistant speech request failed with status ${response.status}`));
    }
    const blob = await response.blob();
    const fetchEndedAt = Date.now();
    const objectUrl = URL.createObjectURL(blob);
    const audio = new Audio(objectUrl);
    audio.preload = 'auto';
    return { audio, objectUrl, fetchStartedAt, fetchEndedAt };
  }

  async startAssistantSpeech(sessionId: string, messageId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/messages/${messageId}/speech/start`, {
      method: 'POST',
      body: '',
      isJsonRequest: false,
    });
  }

  async stopAssistantSpeech(sessionId: string, messageId: string, reason: string): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/messages/${messageId}/speech/stop`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }

  async startVoiceTurn(sessionId: string, deviceId: string | null, traceId?: string): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/voice-turns/ptt/start`, {
      method: 'POST',
      body: '',
      isJsonRequest: false,
      headers: {
        ...(deviceId ? { 'X-AskChip-Device-Id': deviceId } : {}),
        ...(traceId ? { 'X-AskChip-Trace-Id': traceId } : {}),
      },
    });
  }

  async cancelVoiceTurn(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/voice-turns/ptt/cancel`, {
      method: 'POST',
      body: '',
      isJsonRequest: false,
    });
  }

  async createVoiceTurn(sessionId: string, payload: { blob: Blob; filename: string; deviceId: string | null; durationMs: number; traceId?: string; }): Promise<CreateTurnResponse> {
    return this.request<CreateTurnResponse>(`/api/v1/sessions/${sessionId}/voice-turns?filename=${encodeURIComponent(payload.filename)}`, {
      method: 'POST',
      body: payload.blob,
      isJsonRequest: false,
      headers: {
        'Content-Type': payload.blob.type || 'audio/webm',
        'X-AskChip-Duration-Ms': String(payload.durationMs),
        ...(payload.deviceId ? { 'X-AskChip-Device-Id': payload.deviceId } : {}),
        ...(payload.traceId ? { 'X-AskChip-Trace-Id': payload.traceId } : {}),
      },
    });
  }

  async listSessionArtifacts(sessionId: string): Promise<VmwareArtifactRecord[]> {
    const response = await this.request<{ items: VmwareArtifactRecord[] }>(`/api/v1/sessions/${sessionId}/artifacts`);
    return response.items;
  }

  async uploadSessionArtifact(sessionId: string, file: File, traceId?: string): Promise<VmwareArtifactRecord> {
    const response = await this.request<{ artifact: VmwareArtifactRecord }>(`/api/v1/sessions/${sessionId}/artifacts`, {
      method: 'POST',
      body: file,
      isJsonRequest: false,
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-Artifact-Filename': file.name,
        ...(traceId ? { 'X-AskChip-Trace-Id': traceId } : {}),
      },
    });
    return response.artifact;
  }


  private async getErrorDetail(response: Response, fallbackDetail: string): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        return body.detail;
      }
    } catch {
      // ignored: non-JSON error responses keep the default detail message
    }
    return fallbackDetail;
  }

  private async request<T>(path: string, init?: RequestInit & { isJsonRequest?: boolean; timeoutMs?: number; timeoutMessage?: string }): Promise<T> {
    const timeoutMs = init?.timeoutMs;
    const timeoutMessage = init?.timeoutMessage ?? 'Request timed out.';
    const controller = timeoutMs ? new AbortController() : null;
    const timerId = timeoutMs
      ? setTimeout(() => {
          controller?.abort();
        }, timeoutMs)
      : null;

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        signal: init?.signal ?? controller?.signal,
        headers: init?.isJsonRequest === false
          ? init?.headers
          : {
              'Content-Type': 'application/json',
              ...(init?.headers ?? {}),
            },
      });
    } catch (error) {
      if (timerId !== null) {
        clearTimeout(timerId);
      }
      if (error instanceof DOMException && error.name === 'AbortError' && timeoutMs) {
        throw new ApiTimeoutError(timeoutMessage);
      }
      throw error;
    }

    if (timerId !== null) {
      clearTimeout(timerId);
    }

    if (!response.ok) {
      throw new ApiError(response.status, await this.getErrorDetail(response, `Request failed with status ${response.status}`));
    }

    return (await response.json()) as T;
  }
}

export const askChipApiClient = new AskChipApiClient();
