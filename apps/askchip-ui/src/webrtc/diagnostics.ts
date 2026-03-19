import type { WebRtcConnectionState, WebRtcDiagnosticsSnapshot } from './types.js';

export function mapPeerConnectionState(peer: RTCPeerConnection): WebRtcConnectionState {
  if (peer.connectionState === 'connected') {
    return 'connected';
  }
  if (peer.connectionState === 'failed') {
    return 'failed';
  }
  if (peer.connectionState === 'disconnected' || peer.connectionState === 'closed') {
    return 'disconnected';
  }
  if (peer.connectionState === 'connecting' || peer.connectionState === 'new') {
    return 'connecting';
  }
  return 'idle';
}

export function createDiagnostics(
  peer: RTCPeerConnection | null,
  overrides: Partial<WebRtcDiagnosticsSnapshot> = {},
): WebRtcDiagnosticsSnapshot {
  return {
    sessionId: overrides.sessionId ?? null,
    connectionState: overrides.connectionState ?? (peer ? mapPeerConnectionState(peer) : 'idle'),
    iceConnectionState: overrides.iceConnectionState ?? peer?.iceConnectionState ?? 'new',
    signalingState: overrides.signalingState ?? peer?.signalingState ?? 'stable',
    lastError: overrides.lastError ?? null,
  };
}
