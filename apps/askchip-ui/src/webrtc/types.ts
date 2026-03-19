export type WebRtcConnectionState = 'idle' | 'preparing' | 'connecting' | 'connected' | 'disconnected' | 'failed' | 'unsupported';

export interface WebRtcDiagnosticsSnapshot {
  sessionId: string | null;
  connectionState: WebRtcConnectionState;
  iceConnectionState: RTCIceConnectionState | 'new';
  signalingState: RTCSignalingState | 'stable';
  lastError: string | null;
}

export interface WebRtcOfferResponse {
  session_id: string;
  answer: { type: 'answer'; sdp: string } | null;
  status: 'answer_created' | 'unsupported';
  detail: string;
}
