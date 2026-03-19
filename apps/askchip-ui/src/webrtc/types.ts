export type WebRtcConnectionState = 'idle' | 'preparing' | 'connecting' | 'connected' | 'disconnected' | 'failed' | 'unsupported';

export interface WebRtcDiagnosticsSnapshot {
  sessionId: string | null;
  connectionState: WebRtcConnectionState;
  iceConnectionState: RTCIceConnectionState | 'new';
  signalingState: RTCSignalingState | 'stable';
  lastError: string | null;
}

export interface WebRtcSignalEnvelope {
  event: 'offer' | 'disconnect';
  session_id: string | null;
  offer?: { type: RTCSdpType; sdp: string };
}

export interface WebRtcSignalResponse {
  session_id: string;
  event: 'answer' | 'disconnected' | 'error';
  status: 'answer_created' | 'unsupported' | 'error' | 'disconnected' | string;
  detail: string;
  answer: { type: 'answer'; sdp: string } | null;
}
