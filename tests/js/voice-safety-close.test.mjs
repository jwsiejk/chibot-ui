import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Blob as NodeBlob } from 'node:buffer';

if (typeof Blob === 'undefined') {
  globalThis.Blob = NodeBlob;
}

const listenerMap = new Map();

class TestCustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

globalThis.CustomEvent = TestCustomEvent;

const windowStub = {
  __askchip_config: {},
  addEventListener(type, handler) {
    if (!listenerMap.has(type)) listenerMap.set(type, new Set());
    listenerMap.get(type).add(handler);
  },
  removeEventListener(type, handler) {
    const set = listenerMap.get(type);
    if (!set) return;
    set.delete(handler);
    if (set.size === 0) listenerMap.delete(type);
  },
  dispatchEvent(event) {
    const set = listenerMap.get(event.type);
    if (!set) return true;
    for (const handler of Array.from(set)) {
      handler(event);
    }
    return true;
  },
};

windowStub.CustomEvent = TestCustomEvent;
windowStub.dispatchEvent = windowStub.dispatchEvent.bind(windowStub);

globalThis.window = windowStub;

globalThis.performance = {
  now: () => Date.now(),
};

globalThis.__TEST_WS_HOOKS = {};

const wsStubModule = await import('./ws_stub.mjs');
globalThis.__TEST_WS_MODULE = wsStubModule;

test('safety close timer emits CloseStream when no VAD end arrives', async () => {
  const voice = await import('../../static/js/voice.js');
  const hooks = voice.__TEST_ONLY__;
  const wsModule = await import('../../static/js/ws_module.js');
  await wsModule.getWSModule();

  hooks.clearSafetyCloseTimer();
  hooks.state.turnOpen = true;
  hooks.state.recStreaming = true;
  hooks.state.turnClosePromise = null;
  hooks.state.chunkSendPromise = Promise.resolve();
  hooks.state.chunkBytesSent = 0;
  hooks.state.chunkSendError = null;
  hooks.state.turnHintSent = true; // avoid sending AudioStart noise in this test
  hooks.state.lastChunkAt = 0;

  windowStub.__askchip_config.chunk_safety_timeout_ms = 25;

  let closeCalls = 0;
  let chunkCalls = 0;

  globalThis.__TEST_WS_HOOKS = {
    onSendAudioChunk: async () => {
      chunkCalls += 1;
      return true;
    },
    onSendCloseStream: () => {
      closeCalls += 1;
      return true;
    },
  };

  const blob = new Blob(['frame'], { type: 'audio/webm; codecs=opus' });
  hooks.sendRecorderChunk(blob);

  await hooks.state.chunkSendPromise;
  assert.equal(chunkCalls, 1, 'chunk should be uploaded immediately');
  assert.equal(closeCalls, 0, 'close should not fire before the safety timer');
  assert.ok(hooks.state.lastChunkAt > 0, 'lastChunkAt must update after chunk send');

  await new Promise((resolve) => setTimeout(resolve, 60));

  assert.equal(closeCalls, 1, 'CloseStream should be sent after the safety timer elapses');
  assert.equal(hooks.state.turnOpen, false, 'turn should be marked closed');
  assert.equal(hooks.state.safetyCloseTimer, null, 'safety timer should clear after firing');

  hooks.clearSafetyCloseTimer();
  hooks.state.recStreaming = false;
  hooks.state.turnClosePromise = null;
  hooks.state.turnOpen = false;
  hooks.state.lastChunkAt = 0;
  delete windowStub.__askchip_config.chunk_safety_timeout_ms;
  globalThis.__TEST_WS_HOOKS = {};
});
