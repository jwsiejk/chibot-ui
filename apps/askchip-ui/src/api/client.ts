import type {
  ConfigResponse,
  ReadinessResponse,
  CreateSessionRequest,
  CreateTurnRequest,
  CreateTurnResponse,
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

export class AskChipApiClient {
  readonly baseUrl: string;

  constructor(baseUrl = runtimeConfig.apiBaseUrl) {
    this.baseUrl = baseUrl;
  }

  async getConfig(): Promise<ConfigResponse> {
    return this.request<ConfigResponse>('/api/v1/config');
  }

  async getReadiness(): Promise<ReadinessResponse> {
    return this.request<ReadinessResponse>('/api/v1/readiness');
  }

  async listSessions(): Promise<SessionRecord[]> {
    const response = await this.request<SessionsResponse>('/api/v1/sessions');
    return response.items;
  }

  async createSession(payload: CreateSessionRequest): Promise<SessionRecord> {
    return this.request<SessionRecord>('/api/v1/sessions', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getTranscript(sessionId: string): Promise<TranscriptResponse> {
    return this.request<TranscriptResponse>(`/api/v1/sessions/${sessionId}/transcript`);
  }

  async deleteSession(sessionId: string): Promise<{ status: string; session_id: string; }> {
    return this.request<{ status: string; session_id: string; }>(`/api/v1/sessions/${sessionId}`, {
      method: 'DELETE',
    });
  }

  async createTurn(sessionId: string, payload: CreateTurnRequest): Promise<CreateTurnResponse> {
    return this.request<CreateTurnResponse>(`/api/v1/sessions/${sessionId}/turns`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getAssistantSpeech(sessionId: string, messageId: string, text?: string): Promise<{ audio: HTMLAudioElement; objectUrl: string; }> {
    const search = text ? `?text=${encodeURIComponent(text)}` : '';
    const response = await fetch(`${this.baseUrl}/api/v1/sessions/${sessionId}/messages/${messageId}/speech${search}`);
    if (!response.ok) {
      throw new ApiError(response.status, await this.getErrorDetail(response, `Assistant speech request failed with status ${response.status}`));
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    return { audio: new Audio(objectUrl), objectUrl };
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

  async startVoiceTurn(sessionId: string, deviceId: string | null): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/voice-turns/ptt/start`, {
      method: 'POST',
      body: '',
      isJsonRequest: false,
      headers: deviceId ? { 'X-AskChip-Device-Id': deviceId } : undefined,
    });
  }

  async cancelVoiceTurn(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${sessionId}/voice-turns/ptt/cancel`, {
      method: 'POST',
      body: '',
      isJsonRequest: false,
    });
  }

  async createVoiceTurn(sessionId: string, payload: { blob: Blob; filename: string; deviceId: string | null; durationMs: number; }): Promise<CreateTurnResponse> {
    return this.request<CreateTurnResponse>(`/api/v1/sessions/${sessionId}/voice-turns?filename=${encodeURIComponent(payload.filename)}`, {
      method: 'POST',
      body: payload.blob,
      isJsonRequest: false,
      headers: {
        'Content-Type': payload.blob.type || 'audio/webm',
        'X-AskChip-Duration-Ms': String(payload.durationMs),
        ...(payload.deviceId ? { 'X-AskChip-Device-Id': payload.deviceId } : {}),
      },
    });
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

  private async request<T>(path: string, init?: RequestInit & { isJsonRequest?: boolean }): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: init?.isJsonRequest === false
        ? init?.headers
        : {
            'Content-Type': 'application/json',
            ...(init?.headers ?? {}),
          },
    });

    if (!response.ok) {
      throw new ApiError(response.status, await this.getErrorDetail(response, `Request failed with status ${response.status}`));
    }

    return (await response.json()) as T;
  }
}

export const askChipApiClient = new AskChipApiClient();
