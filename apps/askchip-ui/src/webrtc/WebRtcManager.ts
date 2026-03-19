import { askChipSignalingClient } from './signalingClient';
import type { WebRtcDiagnosticsSnapshot } from './types';

export class WebRtcManager {
  private peer: RTCPeerConnection | null = null;
  private sessionId: string | null = null;

  constructor(private readonly onDiagnostics: (value: WebRtcDiagnosticsSnapshot) => void) {}

  async connect(stream: MediaStream, sessionId: string | null): Promise<void> {
    this.disconnect();
    this.onDiagnostics({ sessionId: null, connectionState: 'preparing', iceConnectionState: 'new', signalingState: 'stable', lastError: null });
    const peer = new RTCPeerConnection();
    this.peer = peer;
    this.sessionId = sessionId;

    stream.getAudioTracks().forEach((track) => peer.addTrack(track, stream));

    peer.addEventListener('iceconnectionstatechange', () => {
      this.onDiagnostics({
        sessionId: this.sessionId,
        connectionState: peer.iceConnectionState === 'connected' || peer.iceConnectionState === 'completed'
          ? 'connected'
          : peer.iceConnectionState === 'failed'
            ? 'failed'
            : peer.iceConnectionState === 'disconnected'
              ? 'disconnected'
              : 'connecting',
        iceConnectionState: peer.iceConnectionState,
        signalingState: peer.signalingState,
        lastError: null,
      });
    });

    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    this.onDiagnostics({ sessionId: this.sessionId, connectionState: 'connecting', iceConnectionState: peer.iceConnectionState, signalingState: peer.signalingState, lastError: null });

    const response = await askChipSignalingClient.sendOffer({
      sessionId,
      sdp: offer.sdp ?? '',
      type: offer.type,
    });

    this.sessionId = response.session_id;
    if (response.status === 'unsupported' || !response.answer?.sdp) {
      this.onDiagnostics({
        sessionId: response.session_id,
        connectionState: 'unsupported',
        iceConnectionState: peer.iceConnectionState,
        signalingState: peer.signalingState,
        lastError: response.detail,
      });
      return;
    }

    await peer.setRemoteDescription(response.answer);
    this.onDiagnostics({
      sessionId: response.session_id,
      connectionState: 'connecting',
      iceConnectionState: peer.iceConnectionState,
      signalingState: peer.signalingState,
      lastError: null,
    });
  }

  disconnect(): void {
    this.peer?.close();
    this.peer = null;
    this.sessionId = null;
  }
}
