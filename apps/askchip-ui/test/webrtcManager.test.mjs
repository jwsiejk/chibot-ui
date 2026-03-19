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
      { exchange: async () => ({ session_id: 'rtc-1', event: 'answer', status: 'unsupported', detail: 'aiortc missing', answer: null }) },
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
      { exchange: async () => { throw new Error('socket failed'); } },
      () => peer,
    );

    await assert.rejects(() => manager.connect(buildStream(), 'session-1'), /socket failed/);
    assert.equal(peer.closed, true);
    assert.equal(snapshots.at(-1).connectionState, 'failed');
    assert.equal(snapshots.at(-1).lastError, 'socket failed');
  });
});
