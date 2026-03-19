import { runtimeConfig } from '../config/runtime.js';
import type { WebRtcSignalEnvelope, WebRtcSignalResponse } from './types.js';

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
}

export const askChipSignalingClient = new AskChipSignalingClient();
