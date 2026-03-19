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

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
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
