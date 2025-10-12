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

if (typeof globalThis.window === 'undefined') {
  globalThis.window = windowStub;
} else {
  Object.assign(globalThis.window, windowStub);
}

let nowValue = 0;

globalThis.performance = { now: () => nowValue };

class AudioStub {
  constructor() {
    this.autoplay = true;
    this.volume = 1;
    this.paused = true;
    this._listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    const set = this._listeners.get(type);
    if (!set) return;
    set.delete(handler);
    if (set.size === 0) this._listeners.delete(type);
  }

  pause() {
    this.paused = true;
    this._emit('pause');
  }

  play() {
    this.paused = false;
    this._emit('playing');
  }

  _emit(type) {
    const set = this._listeners.get(type);
    if (!set) return;
    for (const handler of Array.from(set)) {
      try { handler(); } catch {}
    }
  }
}

globalThis.Audio = AudioStub;

globalThis.__TEST_WS_HOOKS = {};
const wsStubModule = await import('./ws_stub.mjs');
globalThis.__TEST_WS_MODULE = wsStubModule;

const voiceModule = await import('../../static/js/voice.js');
const hooks = voiceModule.__TEST_ONLY__;

function resetState() {
  hooks.state.postTtsHoldUntil = 0;
  if (hooks.state.postTtsHoldTimer) {
    clearTimeout(hooks.state.postTtsHoldTimer);
    hooks.state.postTtsHoldTimer = null;
  }
  hooks.state.turnOpen = false;
  hooks.state.stream = null;
}

resetState();

test('speech start waits for post-TTS hold before barging', async () => {
  resetState();
  hooks.state.turnOpen = true;

  const closeEvents = [];
  globalThis.__TEST_WS_HOOKS = {
    onSendCloseStream: () => {
      closeEvents.push({ at: nowValue });
      return true;
    },
  };

  nowValue = 0;
  window.dispatchEvent(new CustomEvent('chip-tts', { detail: { state: 'playing' } }));
  assert.ok(hooks.state.postTtsHoldUntil > nowValue, 'hold window should be scheduled on playing');

  nowValue = hooks.state.postTtsHoldUntil - 10;
  hooks.onSpeechStartCommitted();

  assert.equal(closeEvents.length, 0, 'barge-in should be deferred while hold is active');
  assert.ok(hooks.state.postTtsHoldTimer, 'a retry timer should be scheduled while hold is active');

  setTimeout(() => {
    nowValue = hooks.state.postTtsHoldUntil + 5;
  }, 5);

  await new Promise((resolve) => setTimeout(resolve, 25));

  assert.equal(closeEvents.length, 1, 'barge-in should occur once the hold expires');
  assert.equal(hooks.state.turnOpen, false, 'turn should be closed by barge-in');
  assert.equal(hooks.state.postTtsHoldTimer, null, 'retry timer should be cleared after firing');

  globalThis.__TEST_WS_HOOKS = {};
  listenerMap.clear();
  resetState();
});
