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

class FakeRecorder {
  constructor() {
    this.state = 'inactive';
    this.mimeType = 'audio/webm; codecs=opus';
    this.ondataavailable = null;
    this.onstop = null;
    this.stopCalled = false;
    FakeRecorder.instances.push(this);
  }

  start() {
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    this.stopCalled = true;
  }
}

FakeRecorder.instances = [];

globalThis.MediaRecorder = FakeRecorder;

async function setupVoiceModule() {
  const voice = await import('../../static/js/voice.js');
  const hooks = voice.__TEST_ONLY__;
  const wsModule = await import('../../static/js/ws_module.js');
  await wsModule.getWSModule();
  return { voice, hooks };
}

function resetHooks(hooks) {
  hooks.state.turnOpen = false;
  hooks.state.turnClosePromise = null;
  hooks.state.chunkSendPromise = Promise.resolve();
  hooks.state.chunkBytesSent = 0;
  hooks.state.chunkSendError = null;
  hooks.state.turnTimer = null;
  hooks.state.pendingEndTimer = null;
  hooks.state.safetyCloseTimer = null;
  hooks.state.turnHintSent = false;
  hooks.state.turnHintMime = null;
  hooks.state.turnHintPromise = null;
  hooks.state.turnHintAwaitingWS = false;
  hooks.state.rec = null;
  hooks.state.recStreaming = false;
  hooks.state.stream = { active: true };
  hooks.state.preRollBlobs = [];
  hooks.state.preRollDurationMs = 0;
  hooks.state.wsListener = null;
  hooks.state.greetGateActive = false;
  hooks.state.greetGatePhase = 'idle';
  hooks.state.greetGateWaiters = [];
  hooks.state.greetGateCalibrateTimer = null;
  hooks.state.greetGateCalibrateUntil = 0;
  hooks.state.greetGateCalibrateLastMs = null;
}

async function cleanup(hooks, voice) {
  const recorder = FakeRecorder.instances.at(-1);
  hooks.stopRecorder({ reason: 'test_cleanup' });
  if (recorder && typeof recorder.onstop === 'function') {
    await recorder.onstop();
  }
  if (hooks.state.turnTimer) {
    clearTimeout(hooks.state.turnTimer);
    hooks.state.turnTimer = null;
  }
  hooks.state.wsListener = null;
  hooks.state.chunkSendPromise = Promise.resolve();
  FakeRecorder.instances = [];
  globalThis.__TEST_WS_HOOKS = {};
  voice.setGreetGateActive(false);
  listenerMap.clear();
}

test('AudioStart waits for greet gate release after UtteranceEnd calibration', async () => {
  const { voice, hooks } = await setupVoiceModule();
  resetHooks(hooks);

  const preRollBlob = new Blob(['pre'], { type: 'audio/webm; codecs=opus' });
  hooks.state.preRollBlobs = [{ blob: preRollBlob, durationMs: 25, timecode: 25 }];
  hooks.state.preRollDurationMs = 25;
  hooks.state.greetGateCalibrateMinMs = 5;
  hooks.state.greetGateCalibrateMaxMs = 20;
  hooks.state.greetGateCalibrateMs = 10;

  const transportEvents = [];
  globalThis.__TEST_WS_HOOKS = {
    onSendJSON: (payload) => {
      transportEvents.push({ kind: 'json', payload });
      return true;
    },
    onSendAudioChunk: (blob) => {
      transportEvents.push({ kind: 'chunk', payload: blob });
      return true;
    },
  };

  voice.setGreetGateActive(true);

  const startPromise = hooks.startRecorder();
  await Promise.resolve();
  assert.equal(transportEvents.length, 0, 'AudioStart should not send before greet gate release');

  window.dispatchEvent(new CustomEvent('askchip-ws', { detail: { type: 'UtteranceEnd' } }));

  await new Promise((resolve) => setTimeout(resolve, 15));

  const started = await startPromise;
  assert.equal(started, true, 'recorder should start after greet gate release');
  await hooks.state.chunkSendPromise;

  const audioStartEvents = transportEvents.filter((ev) => ev.kind === 'json' && ev.payload?.type === 'AudioStart');
  assert.ok(audioStartEvents.length >= 1, 'AudioStart must be emitted after greet gate opens');
  assert.ok(audioStartEvents[0].payload.mime, 'AudioStart payload should include MIME');

  await cleanup(hooks, voice);
});

test('greet gate allows barge-in when SNR meets minimum', async () => {
  const { voice, hooks } = await setupVoiceModule();
  resetHooks(hooks);

  const preRollBlob = new Blob(['pre'], { type: 'audio/webm; codecs=opus' });
  hooks.state.preRollBlobs = [{ blob: preRollBlob, durationMs: 25, timecode: 25 }];
  hooks.state.preRollDurationMs = 25;
  hooks.state.ttsPlaying = false;

  const transportEvents = [];
  globalThis.__TEST_WS_HOOKS = {
    onSendJSON: (payload) => {
      transportEvents.push({ kind: 'json', payload });
      return true;
    },
    onSendAudioChunk: (blob) => {
      transportEvents.push({ kind: 'chunk', payload: blob });
      return true;
    },
  };

  voice.setGreetGateActive(true);

  await hooks.onSpeechStartCommitted({ snrDb: 12, thresholds: { startDb: -40 } });
  await hooks.state.chunkSendPromise;

  const audioStartEvents = transportEvents.filter((ev) => ev.kind === 'json' && ev.payload?.type === 'AudioStart');
  assert.ok(audioStartEvents.length >= 1, 'AudioStart should emit during qualified barge-in');
  assert.equal(hooks.state.greetGateActive, false, 'greet gate should release after barge-in');

  await cleanup(hooks, voice);
});

