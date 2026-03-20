import type {
  ConfigResponse,
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

  async createTurn(sessionId: string, payload: CreateTurnRequest): Promise<CreateTurnResponse> {
    return this.request<CreateTurnResponse>(`/api/v1/sessions/${sessionId}/turns`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getAssistantSpeech(sessionId: string, messageId: string): Promise<{ audio: HTMLAudioElement; objectUrl: string; }> {
    const response = await fetch(`${this.baseUrl}/api/v1/sessions/${sessionId}/messages/${messageId}/speech`);
    if (!response.ok) {
      throw new ApiError(response.status, `Assistant speech request failed with status ${response.status}`);
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
      let detail = `Request failed with status ${response.status}`;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) {
          detail = body.detail;
        }
      } catch {
        // ignored: non-JSON error responses keep the default detail message
      }
      throw new ApiError(response.status, detail);
    }

    return (await response.json()) as T;
  }
}

export const askChipApiClient = new AskChipApiClient();
