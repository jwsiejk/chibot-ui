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

class FakeRecorder {
  constructor() {
    this.state = 'inactive';
    this.mimeType = 'audio/ogg; codecs=opus';
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

test('pre-roll audio is flushed before first live MediaRecorder chunk', async () => {
  const voice = await import('../../static/js/voice.js');
  const hooks = voice.__TEST_ONLY__;

  hooks.state.stream = { active: true };
  hooks.state.turnOpen = false;
  hooks.state.turnClosePromise = null;
  hooks.state.chunkSendPromise = Promise.resolve();
  hooks.state.chunkBytesSent = 0;
  hooks.state.chunkSendError = null;

  const preRollBlob = new Blob(['pre'], { type: 'audio/wav' });
  hooks.state.preRollPending = {
    blob: preRollBlob,
    frames: 1200,
    durationMs: 25,
    sampleRate: 48000,
  };

  const events = [];
  globalThis.__TEST_WS_HOOKS = {
    onSendAudioChunk: (blob) => {
      events.push(blob);
    },
  };

  const started = hooks.startRecorder();
  assert.equal(started, true, 'recorder should start with fake stream');
  assert.equal(hooks.state.preRollPending, null, 'pre-roll state should clear once recorder starts');

  const recorder = FakeRecorder.instances.at(-1);
  assert.ok(recorder, 'fake recorder should exist');
  assert.equal(recorder.state, 'recording', 'recorder should be recording');

  const liveBlob = new Blob(['live'], { type: 'audio/ogg; codecs=opus' });
  recorder.ondataavailable({ data: liveBlob });

  await hooks.state.chunkSendPromise;

  assert.equal(events.length, 2, 'both pre-roll and live chunks should be sent');
  const texts = [];
  for (const blob of events) {
    texts.push(await blob.text());
  }
  assert.deepEqual(texts, ['pre', 'live'], 'pre-roll chunk must precede the live chunk');

  hooks.stopRecorder({ reason: 'test_cleanup' });
  if (typeof recorder.onstop === 'function') {
    await recorder.onstop();
  }

  if (hooks.state.turnTimer) {
    clearTimeout(hooks.state.turnTimer);
    hooks.state.turnTimer = null;
  }
  globalThis.__TEST_WS_HOOKS = {};
  FakeRecorder.instances = [];
});
