import { waitForIceGatheringComplete } from './iceGathering.js';
import { askChipSignalingClient, type AskChipSignalingClient } from './signalingClient.js';
import { createDiagnostics, mapPeerConnectionState } from './diagnostics.js';
import type { DisconnectResult, WebRtcDiagnosticsSnapshot, WebRtcSignalResponse } from './types.js';

const REMOTE_DISCONNECT_TIMEOUT_DETAIL = 'Remote WebRTC cleanup timed out; local diagnostics were reset anyway.';
const REMOTE_DISCONNECT_FAILURE_DETAIL = 'Remote WebRTC cleanup failed; local diagnostics were reset anyway.';

export class WebRtcManager {
  private peer: RTCPeerConnection | null = null;
  private sessionId: string | null = null;
  private remoteSessionId: string | null = null;
  private connectAttempt = 0;
  private disconnectPromise: Promise<void> | null = null;

  constructor(
    private readonly onDiagnostics: (value: WebRtcDiagnosticsSnapshot) => void,
    private readonly signalingClient: Pick<AskChipSignalingClient, 'exchange' | 'disconnect'> = askChipSignalingClient,
    private readonly peerFactory: () => RTCPeerConnection = () => new RTCPeerConnection(),
  ) {}

  async connect(stream: MediaStream, sessionId: string | null): Promise<void> {
    await this.disconnect('idle');
    const connectAttempt = ++this.connectAttempt;
    const peer = this.peerFactory();
    this.peer = peer;
    this.sessionId = sessionId;
    this.remoteSessionId = null;
    this.publish(createDiagnostics(peer, { sessionId, connectionState: 'preparing', lastError: null }));

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

      this.publish(createDiagnostics(peer, { sessionId, connectionState: 'connecting', lastError: null }));
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

  async disconnect(finalState: WebRtcDiagnosticsSnapshot['connectionState'] = 'disconnected'): Promise<void> {
    if (this.disconnectPromise) {
      await this.disconnectPromise;
      return;
    }

    const peer = this.peer;
    const sessionId = this.remoteSessionId ?? this.sessionId;
    const remoteSessionId = this.remoteSessionId;
    const hasActiveState = peer !== null || this.sessionId !== null || this.remoteSessionId !== null;

    if (!hasActiveState) {
      return;
    }

    this.connectAttempt += 1;
    this.peer = null;
    this.sessionId = null;
    this.remoteSessionId = null;
    peer?.close();

    this.disconnectPromise = this.finalizeDisconnect(finalState, sessionId, remoteSessionId);
    try {
      await this.disconnectPromise;
    } finally {
      this.disconnectPromise = null;
    }
  }

  private async finalizeDisconnect(
    finalState: WebRtcDiagnosticsSnapshot['connectionState'],
    sessionId: string | null,
    remoteSessionId: string | null,
  ): Promise<void> {
    const remoteResult = remoteSessionId ? await this.signalingClient.disconnect(remoteSessionId) : { timedOut: false, error: null };
    this.publishDisconnectDiagnostics(finalState, sessionId, remoteResult);
  }

  private publishDisconnectDiagnostics(
    finalState: WebRtcDiagnosticsSnapshot['connectionState'],
    sessionId: string | null,
    remoteResult: DisconnectResult,
  ): void {
    const lastError = remoteResult.timedOut
      ? REMOTE_DISCONNECT_TIMEOUT_DETAIL
      : remoteResult.error
        ? `${REMOTE_DISCONNECT_FAILURE_DETAIL} ${remoteResult.error.message}`
        : null;
    this.publish(createDiagnostics(null, { sessionId, connectionState: finalState, lastError }));
  }

  private async applyRemoteResponse(response: WebRtcSignalResponse, peer: RTCPeerConnection, connectAttempt: number): Promise<void> {
    if (!this.isCurrentAttempt(connectAttempt, peer)) {
      return;
    }

    const resolvedSessionId = response.session_id || this.sessionId;
    this.sessionId = resolvedSessionId;
    this.remoteSessionId = response.session_id || null;
    if (response.status !== 'answer_created' || !response.answer?.sdp) {
      this.failAndCleanup(response.detail, peer, connectAttempt, response.status === 'unsupported' ? 'unsupported' : 'failed', resolvedSessionId);
      return;
    }

    await peer.setRemoteDescription(response.answer);
    if (!this.isCurrentAttempt(connectAttempt, peer)) {
      return;
    }
    this.publish(createDiagnostics(peer, { sessionId: resolvedSessionId, connectionState: mapPeerConnectionState(peer), lastError: null }));
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
    this.remoteSessionId = null;
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
