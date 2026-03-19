import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import { AskChipSignalingClient } from '../.test-dist/webrtc/signalingClient.js';

class HangingWebSocket {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closed = false;
    HangingWebSocket.instances.push(this);
  }

  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) ?? [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }

  send() {}

  close() {
    this.closed = true;
    for (const handler of this.listeners.get('close') ?? []) {
      handler();
    }
  }
}

const originalWebSocket = globalThis.WebSocket;

afterEach(() => {
  globalThis.WebSocket = originalWebSocket;
  HangingWebSocket.instances.length = 0;
});

describe('AskChipSignalingClient', () => {
  it('actively closes a hanging disconnect socket when the timeout elapses', async () => {
    globalThis.WebSocket = HangingWebSocket;
    const client = new AskChipSignalingClient('ws://example.test');

    const result = await client.disconnect('rtc-timeout', 5);

    assert.deepEqual(result, { timedOut: true, error: null });
    assert.equal(HangingWebSocket.instances.length, 1);
    assert.equal(HangingWebSocket.instances[0].closed, true);
    assert.equal(HangingWebSocket.instances[0].url, 'ws://example.test/ws/webrtc');
  });
});
