import { test } from 'node:test';
import assert from 'node:assert/strict';

const listenerMap = new Map();

const windowStub = {
  __askchip_config: {
    tts: {
      mask_decay_ms: 0,
      post_play_hold_ms: 0,
      decay_ms: 0,
    },
  },
  addEventListener(type, handler) {
    if (!listenerMap.has(type)) {
      listenerMap.set(type, new Set());
    }
    listenerMap.get(type).add(handler);
  },
  removeEventListener(type, handler) {
    const listeners = listenerMap.get(type);
    if (!listeners) return;
    listeners.delete(handler);
    if (listeners.size === 0) {
      listenerMap.delete(type);
    }
  },
  dispatchEvent(event) {
    const listeners = listenerMap.get(event.type);
    if (!listeners) return true;
    for (const handler of Array.from(listeners)) {
      handler(event);
    }
    return true;
  },
};

const performanceStub = {
  now: () => 0,
};

const localStorageStore = new Map();

const localStorageStub = {
  getItem(key) {
    return localStorageStore.has(key) ? localStorageStore.get(key) : null;
  },
  setItem(key, value) {
    localStorageStore.set(key, String(value));
  },
  removeItem(key) {
    localStorageStore.delete(key);
  },
  clear() {
    localStorageStore.clear();
  },
};

globalThis.localStorage = localStorageStub;

const cryptoStub = { randomUUID: () => 'test-session-id' };
try {
  Object.defineProperty(globalThis, 'crypto', {
    value: cryptoStub,
    configurable: true,
    writable: true,
  });
} catch {
  globalThis.crypto = cryptoStub;
}

globalThis.window = windowStub;
globalThis.performance = performanceStub;
globalThis.console = {
  log: () => {},
  warn: () => {},
  error: () => {},
  info: () => {},
  debug: () => {},
};
globalThis.__TEST_WS_MODULE = {
  openWS: () => Promise.resolve(),
  waitWSOpen: () => Promise.resolve({}),
  isOpen: () => false,
  isConnecting: () => false,
  closeWS: () => {},
  bufferedAmount: () => 0,
  configure: () => {},
  sendJSON: () => true,
  sendAudioChunk: () => Promise.resolve(),
  sendCloseStream: () => {},
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
};

const navigatorStub = {
  mediaDevices: {
    getUserMedia: async () => {
      throw new Error('getUserMedia not available in tests');
    },
  },
};

try {
  Object.defineProperty(globalThis, 'navigator', {
    value: navigatorStub,
    configurable: true,
    writable: true,
  });
} catch {
  globalThis.navigator = navigatorStub;
}

globalThis.ADVANCED_LOGGING_ENABLED = true;

const voiceModule = await import('../../static/js/voice.js');
const { setGreetGateActive, __TEST_ONLY__ } = voiceModule;

const getCtx = () => {
  const ctx = __TEST_ONLY__.getCtx?.();
  if (!ctx) {
    throw new Error('voice runtime context not initialized');
  }
  return ctx;
};

const dispatchWsFrame = (frame) => {
  window.dispatchEvent({ type: 'askchip-ws', detail: frame });
};

test('assistant_audio WS frame engages TTS suppression until utterance end', () => {
  setGreetGateActive(false);
  const ctx = getCtx();

  ctx.audio.micGateReasons.clear();
  ctx.state.ttsPlaying = false;
  if (ctx.maskLogTimer) {
    clearInterval(ctx.maskLogTimer);
    ctx.maskLogTimer = null;
  }
  ctx.ttsMask.clear();

  dispatchWsFrame({ type: 'assistant_audio' });

  assert.equal(ctx.state.ttsPlaying, true, 'ttsPlaying should be true while assistant audio is active');
  assert.equal(ctx.audio.micGateReasons.has('tts_active'), true, 'mic gate should track active TTS reason');

  dispatchWsFrame({ type: 'utteranceend' });

  assert.equal(ctx.state.ttsPlaying, false, 'ttsPlaying should reset after utterance end');
  assert.equal(ctx.audio.micGateReasons.has('tts_active'), false, 'mic gate reason should clear after TTS ends');

  if (ctx.maskLogTimer) {
    clearInterval(ctx.maskLogTimer);
    ctx.maskLogTimer = null;
  }
  ctx.ttsMask.clear();
});
