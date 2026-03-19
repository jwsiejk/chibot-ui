import { waitForIceGatheringComplete } from './iceGathering.js';
import { askChipSignalingClient, type AskChipSignalingClient } from './signalingClient.js';
import { createDiagnostics, mapPeerConnectionState } from './diagnostics.js';
import type { WebRtcDiagnosticsSnapshot, WebRtcSignalResponse } from './types.js';

export class WebRtcManager {
  private peer: RTCPeerConnection | null = null;
  private sessionId: string | null = null;
  private connectAttempt = 0;

  constructor(
    private readonly onDiagnostics: (value: WebRtcDiagnosticsSnapshot) => void,
    private readonly signalingClient: Pick<AskChipSignalingClient, 'exchange'> = askChipSignalingClient,
    private readonly peerFactory: () => RTCPeerConnection = () => new RTCPeerConnection(),
  ) {}

  async connect(stream: MediaStream, sessionId: string | null): Promise<void> {
    this.disconnect('idle');
    const connectAttempt = ++this.connectAttempt;
    const peer = this.peerFactory();
    this.peer = peer;
    this.sessionId = sessionId;
    this.publish(createDiagnostics(peer, { sessionId, connectionState: 'preparing' }));

    const publishPeerDiagnostics = (lastError: string | null = null) => {
      if (!this.isCurrentAttempt(connectAttempt, peer)) {
        return;
      }
      this.publish(createDiagnostics(peer, {
        sessionId: this.sessionId,
        connectionState: mapPeerConnectionState(peer),
        lastError,
      }));
    };

    peer.addEventListener('connectionstatechange', () => publishPeerDiagnostics());
    peer.addEventListener('iceconnectionstatechange', () => publishPeerDiagnostics());
    peer.addEventListener('signalingstatechange', () => publishPeerDiagnostics());

    stream.getAudioTracks().forEach((track) => peer.addTrack(track, stream));

    try {
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await waitForIceGatheringComplete(peer);
      if (!this.isCurrentAttempt(connectAttempt, peer) || !peer.localDescription?.sdp) {
        return;
      }

      this.publish(createDiagnostics(peer, { sessionId, connectionState: 'connecting' }));
      const response = await this.signalingClient.exchange({
        event: 'offer',
        session_id: sessionId,
        offer: {
          type: peer.localDescription.type,
          sdp: peer.localDescription.sdp,
        },
      });

      await this.applyRemoteResponse(response, peer, connectAttempt);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'WebRTC negotiation failed.';
      this.failAndCleanup(message, peer, connectAttempt, 'failed');
      throw error;
    }
  }

  disconnect(finalState: WebRtcDiagnosticsSnapshot['connectionState'] = 'disconnected'): void {
    this.connectAttempt += 1;
    const peer = this.peer;
    const sessionId = this.sessionId;
    this.peer = null;
    this.sessionId = null;
    peer?.close();
    this.publish(createDiagnostics(null, { sessionId, connectionState: finalState }));
  }

  private async applyRemoteResponse(response: WebRtcSignalResponse, peer: RTCPeerConnection, connectAttempt: number): Promise<void> {
    if (!this.isCurrentAttempt(connectAttempt, peer)) {
      return;
    }

    this.sessionId = response.session_id || this.sessionId;
    if (response.status !== 'answer_created' || !response.answer?.sdp) {
      this.failAndCleanup(response.detail, peer, connectAttempt, response.status === 'unsupported' ? 'unsupported' : 'failed', response.session_id);
      return;
    }

    await peer.setRemoteDescription(response.answer);
    if (!this.isCurrentAttempt(connectAttempt, peer)) {
      return;
    }
    this.publish(createDiagnostics(peer, { sessionId: this.sessionId, connectionState: 'connecting' }));
  }

  private failAndCleanup(
    message: string,
    peer: RTCPeerConnection,
    connectAttempt: number,
    connectionState: WebRtcDiagnosticsSnapshot['connectionState'],
    sessionId: string | null = this.sessionId,
  ): void {
    if (!this.isCurrentAttempt(connectAttempt, peer)) {
      return;
    }
    this.peer = null;
    this.sessionId = null;
    peer.close();
    this.publish(createDiagnostics(null, { sessionId, connectionState, lastError: message }));
  }

  private isCurrentAttempt(connectAttempt: number, peer: RTCPeerConnection): boolean {
    return this.connectAttempt === connectAttempt && this.peer === peer;
  }

  private publish(value: WebRtcDiagnosticsSnapshot): void {
    this.onDiagnostics(value);
  }
}
