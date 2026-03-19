import { runtimeConfig } from '../config/runtime';
import type { WebRtcOfferResponse } from './types';

export class AskChipSignalingClient {
  constructor(private readonly baseUrl = runtimeConfig.apiBaseUrl) {}

  async sendOffer(payload: { sessionId: string | null; sdp: string; type: RTCSdpType }): Promise<WebRtcOfferResponse> {
    const response = await fetch(`${this.baseUrl}/api/v1/webrtc/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: payload.sessionId, offer: { sdp: payload.sdp, type: payload.type } }),
    });
    const body = (await response.json()) as WebRtcOfferResponse & { detail?: string };
    if (!response.ok) {
      throw new Error(body.detail ?? `WebRTC signaling failed with status ${response.status}`);
    }
    return body;
  }
}

export const askChipSignalingClient = new AskChipSignalingClient();
