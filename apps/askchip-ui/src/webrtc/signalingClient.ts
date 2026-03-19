import { runtimeConfig } from '../config/runtime.js';
import type { DisconnectResult, WebRtcSignalEnvelope, WebRtcSignalResponse } from './types.js';

const DEFAULT_DISCONNECT_TIMEOUT_MS = 750;

export class AskChipSignalingClient {
  constructor(private readonly baseUrl = runtimeConfig.wsBaseUrl) {}

  async exchange(payload: WebRtcSignalEnvelope): Promise<WebRtcSignalResponse> {
    const socket = new WebSocket(`${this.baseUrl}/ws/webrtc`);

    return new Promise<WebRtcSignalResponse>((resolve, reject) => {
      let settled = false;

      const finalize = (callback: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        callback();
      };

      socket.addEventListener('open', () => {
        socket.send(JSON.stringify(payload));
      });

      socket.addEventListener('message', (event) => {
        finalize(() => {
          try {
            const body = JSON.parse(String(event.data)) as WebRtcSignalResponse;
            socket.close();
            resolve(body);
          } catch (error) {
            socket.close();
            reject(error instanceof Error ? error : new Error('Unable to parse WebRTC signaling response.'));
          }
        });
      });

      socket.addEventListener('error', () => {
        finalize(() => {
          socket.close();
          reject(new Error('WebRTC signaling WebSocket failed before negotiation completed.'));
        });
      });

      socket.addEventListener('close', () => {
        finalize(() => {
          reject(new Error('WebRTC signaling WebSocket closed before negotiation completed.'));
        });
      });
    });
  }

  async disconnect(sessionId: string | null, timeoutMs = DEFAULT_DISCONNECT_TIMEOUT_MS): Promise<DisconnectResult> {
    if (!sessionId) {
      return { timedOut: false, error: null };
    }

    const timeout = new Promise<DisconnectResult>((resolve) => {
      setTimeout(() => resolve({ timedOut: true, error: null }), timeoutMs);
    });

    return Promise.race([
      this.exchange({
        event: 'disconnect',
        session_id: sessionId,
      }).then(() => ({ timedOut: false, error: null })).catch((error) => ({
        timedOut: false,
        error: error instanceof Error ? error : new Error('WebRTC remote disconnect failed.'),
      })),
      timeout,
    ]);
  }
}

export const askChipSignalingClient = new AskChipSignalingClient();
