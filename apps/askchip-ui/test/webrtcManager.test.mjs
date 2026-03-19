import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { WebRtcManager } from '../.test-dist/webrtc/WebRtcManager.js';

class FakePeerConnection {
  constructor() {
    this.connectionState = 'new';
    this.iceConnectionState = 'new';
    this.signalingState = 'stable';
    this.iceGatheringState = 'complete';
    this.localDescription = null;
    this.remoteDescription = null;
    this.closed = false;
    this.listeners = new Map();
  }

  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) ?? [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }

  removeEventListener(name, handler) {
    const handlers = this.listeners.get(name) ?? [];
    this.listeners.set(name, handlers.filter((candidate) => candidate !== handler));
  }

  emit(name) {
    for (const handler of this.listeners.get(name) ?? []) {
      handler();
    }
  }

  addTrack() {}

  async createOffer() {
    return { type: 'offer', sdp: 'offer-sdp' };
  }

  async setLocalDescription(description) {
    this.localDescription = description;
    this.signalingState = 'have-local-offer';
    this.emit('signalingstatechange');
  }

  async setRemoteDescription(description) {
    this.remoteDescription = description;
    this.signalingState = 'stable';
    this.connectionState = 'connecting';
    this.emit('signalingstatechange');
    this.emit('connectionstatechange');
  }

  close() {
    this.closed = true;
    this.connectionState = 'closed';
    this.iceConnectionState = 'closed';
  }
}

function buildStream() {
  return {
    getAudioTracks() {
      return [{ kind: 'audio' }];
    },
  };
}

describe('WebRtcManager', () => {
  it('cleans up and reports unsupported when signaling cannot produce an answer', async () => {
    const snapshots = [];
    const peer = new FakePeerConnection();
    const manager = new WebRtcManager(
      (snapshot) => snapshots.push(snapshot),
      {
        exchange: async () => ({ session_id: 'rtc-1', event: 'answer', status: 'unsupported', detail: 'aiortc missing', answer: null }),
        disconnect: async () => ({ timedOut: false, error: null }),
      },
      () => peer,
    );

    await manager.connect(buildStream(), 'session-1');

    assert.equal(peer.closed, true);
    assert.deepEqual(snapshots.at(-1), {
      sessionId: 'rtc-1',
      connectionState: 'unsupported',
      iceConnectionState: 'new',
      signalingState: 'stable',
      lastError: 'aiortc missing',
    });
  });

  it('closes the peer and reports failed when signaling throws', async () => {
    const snapshots = [];
    const peer = new FakePeerConnection();
    const manager = new WebRtcManager(
      (snapshot) => snapshots.push(snapshot),
      { exchange: async () => { throw new Error('socket failed'); }, disconnect: async () => ({ timedOut: false, error: null }) },
      () => peer,
    );

    await assert.rejects(() => manager.connect(buildStream(), 'session-1'), /socket failed/);
    assert.equal(peer.closed, true);
    assert.equal(snapshots.at(-1).connectionState, 'failed');
    assert.equal(snapshots.at(-1).lastError, 'socket failed');
  });

  it('disconnects the negotiated remote session before clearing local diagnostics', async () => {
    const snapshots = [];
    const peer = new FakePeerConnection();
    const disconnectCalls = [];
    const manager = new WebRtcManager(
      (snapshot) => snapshots.push(snapshot),
      {
        exchange: async () => ({
          session_id: 'rtc-remote-1',
          event: 'answer',
          status: 'answer_created',
          detail: 'ok',
          answer: { type: 'answer', sdp: 'answer-sdp' },
        }),
        disconnect: async (sessionId) => {
          disconnectCalls.push(sessionId);
          return { timedOut: false, error: null };
        },
      },
      () => peer,
    );

    await manager.connect(buildStream(), 'session-1');
    await manager.disconnect();

    assert.deepEqual(disconnectCalls, ['rtc-remote-1']);
    assert.equal(peer.closed, true);
    assert.deepEqual(snapshots.at(-1), {
      sessionId: 'rtc-remote-1',
      connectionState: 'disconnected',
      iceConnectionState: 'new',
      signalingState: 'stable',
      lastError: null,
    });
  });

  it('treats repeated disconnect calls as a safe no-op after the first cleanup', async () => {
    const snapshots = [];
    const peer = new FakePeerConnection();
    const disconnectCalls = [];
    const manager = new WebRtcManager(
      (snapshot) => snapshots.push(snapshot),
      {
        exchange: async () => ({
          session_id: 'rtc-remote-repeat',
          event: 'answer',
          status: 'answer_created',
          detail: 'ok',
          answer: { type: 'answer', sdp: 'answer-sdp' },
        }),
        disconnect: async (sessionId) => {
          disconnectCalls.push(sessionId);
          return { timedOut: false, error: null };
        },
      },
      () => peer,
    );

    await manager.connect(buildStream(), 'session-1');
    await manager.disconnect();
    const snapshotCountAfterFirstDisconnect = snapshots.length;
    await manager.disconnect();

    assert.deepEqual(disconnectCalls, ['rtc-remote-repeat']);
    assert.equal(snapshots.length, snapshotCountAfterFirstDisconnect);
  });

  it('does not reuse a stale remote session id after a failed negotiation retry', async () => {
    const snapshots = [];
    const peers = [new FakePeerConnection(), new FakePeerConnection()];
    const exchangeCalls = [];
    let attempt = 0;
    const manager = new WebRtcManager(
      (snapshot) => snapshots.push(snapshot),
      {
        exchange: async (payload) => {
          exchangeCalls.push(payload.session_id);
          attempt += 1;
          if (attempt === 1) {
            return {
              session_id: 'rtc-failed-1',
              event: 'answer',
              status: 'error',
              detail: 'negotiation failed',
              answer: null,
            };
          }
          return {
            session_id: 'rtc-failed-2',
            event: 'answer',
            status: 'answer_created',
            detail: 'ok',
            answer: { type: 'answer', sdp: 'answer-sdp-2' },
          };
        },
        disconnect: async () => ({ timedOut: false, error: null }),
      },
      () => peers.shift(),
    );

    await manager.connect(buildStream(), 'typed-session');
    await manager.connect(buildStream(), 'typed-session');

    assert.deepEqual(exchangeCalls, ['typed-session', 'typed-session']);
    assert.equal(snapshots.at(-1).sessionId, 'rtc-failed-2');
    assert.equal(snapshots.at(-1).connectionState, 'connecting');
    assert.notEqual(snapshots.at(-1).sessionId, 'rtc-failed-1');
  });
});
