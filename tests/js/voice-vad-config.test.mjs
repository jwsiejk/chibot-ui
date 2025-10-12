import test from 'node:test';
import assert from 'node:assert/strict';

class TestCustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

const listenerMap = new Map();

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

class FakeAnalyser {
  constructor() {
    this.fftSize = 2048;
    this.smoothingTimeConstant = 0.06;
  }

  connect() {}
  disconnect() {}

  getFloatTimeDomainData(arr) {
    if (Array.isArray(arr) || ArrayBuffer.isView(arr)) {
      arr.fill(0);
    }
  }
}

class FakeSource {
  connect() {}
  disconnect() {}
}

class FakeAudioContext {
  constructor() {
    this.state = 'running';
    this.sampleRate = 48000;
    this.destination = { channelCount: 1 };
  }

  async resume() {}
  createMediaStreamSource() { return new FakeSource(); }
  createAnalyser() { return new FakeAnalyser(); }
  close() {}
}

const fakeStream = {
  active: true,
  getAudioTracks() {
    return [{
      label: 'Fake Mic',
      getSettings: () => ({ sampleRate: 48000, channelCount: 1 }),
    }];
  },
};

Object.defineProperty(globalThis, 'CustomEvent', { value: TestCustomEvent, configurable: true });
Object.defineProperty(globalThis, 'window', { value: windowStub, configurable: true });
Object.defineProperty(globalThis, 'performance', { value: { now: () => Date.now() }, configurable: true });
Object.defineProperty(globalThis, 'navigator', {
  value: {
    mediaDevices: {
      async getUserMedia() { return fakeStream; },
    },
  },
  configurable: true,
});
Object.defineProperty(globalThis, '__TEST_WS_HOOKS', { value: {}, configurable: true });
Object.defineProperty(globalThis, '__TEST_WS_MODULE', {
  value: await import('./ws_stub.mjs'),
  configurable: true,
});
Object.defineProperty(globalThis, 'AudioContext', { value: FakeAudioContext, configurable: true });
Object.defineProperty(globalThis, 'webkitAudioContext', { value: FakeAudioContext, configurable: true });
windowStub.AudioContext = FakeAudioContext;
windowStub.webkitAudioContext = FakeAudioContext;

const voice = await import(new URL('../../static/js/voice.js', import.meta.url));
const hooks = voice.__TEST_ONLY__;

test('VAD applies injected config thresholds and boosts', async (t) => {
  windowStub.__askchip_config.vad = {
    baseThresholdDb: 11,
    exitThresholdDb: 7,
    ttsBoostDb: 5,
    minSpeechMs: 420,
  };

  await voice.armVAD();
  t.after(() => {
    voice.disarmVAD();
    hooks.state.stream = fakeStream;
  });

  const vad = hooks.state.vad;
  assert.ok(vad, 'VAD instance should be created');
  assert.equal(vad.opts.minSpeechMs, 420, 'minSpeechMs should honor injected config');
  assert.equal(vad.opts.startDbOffset, 11, 'base threshold should map to startDbOffset');
  assert.equal(vad.opts.stopDbOffset, 7, 'exit threshold should map to stopDbOffset');
  assert.equal(vad.opts.echoBoostStartDb, 5, 'TTS boost should map to echoBoostStartDb');
  assert.equal(vad.opts.echoBoostStopDb, 5, 'TTS boost should map to echoBoostStopDb');

  const quiet = vad._computeThresholds(-72, false);
  const echo = vad._computeThresholds(-72, true);
  assert.equal(
    Math.round((echo.startDb - quiet.startDb) * 10) / 10,
    5,
    'start threshold should increase by TTS boost when echo is present',
  );
  assert.equal(
    Math.round((echo.stopDb - quiet.stopDb) * 10) / 10,
    5,
    'stop threshold should increase by TTS boost when echo is present',
  );
  voice.disarmVAD();
});

test('VAD defaults raise the minimum speech duration', async (t) => {
  windowStub.__askchip_config.vad = {};
  voice.disarmVAD();

  await voice.armVAD();
  t.after(() => {
    voice.disarmVAD();
    hooks.state.stream = fakeStream;
  });

  const vad = hooks.state.vad;
  assert.ok(vad, 'VAD instance should be created with defaults');
  assert.equal(vad.opts.minSpeechMs, 360, 'default minSpeechMs should be 360 ms');
  assert.equal(vad.opts.startDbOffset, 10, 'default base threshold should be 10 dB');
  assert.equal(vad.opts.stopDbOffset, 6, 'default exit threshold should be 6 dB');
  voice.disarmVAD();
});
