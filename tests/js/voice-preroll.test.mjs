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

test('pre-roll audio is flushed before first live MediaRecorder chunk', async () => {
  const voice = await import('../../static/js/voice.js');
  const hooks = voice.__TEST_ONLY__;

  hooks.state.stream = { active: true };
  hooks.state.turnOpen = false;
  hooks.state.turnClosePromise = null;
  hooks.state.chunkSendPromise = Promise.resolve();
  hooks.state.chunkBytesSent = 0;
  hooks.state.chunkSendError = null;

  const preRollBlob = new Blob(['pre'], { type: 'audio/webm; codecs=opus' });
  hooks.state.preRollBlobs = [{ blob: preRollBlob, durationMs: 25, timecode: 25 }];
  hooks.state.preRollDurationMs = 25;

  const transportEvents = [];
  globalThis.__TEST_WS_HOOKS = {
    onSendJSON: (payload) => {
      transportEvents.push({ kind: 'json', payload });
    },
    onSendAudioChunk: (blob) => {
      transportEvents.push({ kind: 'chunk', payload: blob });
    },
  };

  const started = hooks.startRecorder();
  assert.equal(started, true, 'recorder should start with fake stream');
  assert.equal(hooks.state.preRollBlobs.length, 0, 'pre-roll buffer should clear once recorder starts');

  const recorder = FakeRecorder.instances.at(-1);
  assert.ok(recorder, 'fake recorder should exist');
  assert.equal(recorder.state, 'recording', 'recorder should be recording');

  const liveBlob = new Blob(['live'], { type: 'audio/webm; codecs=opus' });
  recorder.ondataavailable({ data: liveBlob });

  await hooks.state.chunkSendPromise;

  assert.ok(transportEvents.length >= 3, 'AudioStart and two chunks should be emitted');
  assert.equal(transportEvents[0]?.kind, 'json', 'AudioStart must be sent before audio chunks');
  assert.equal(transportEvents[0]?.payload?.type, 'AudioStart', 'AudioStart frame should be emitted');
  assert.equal(transportEvents[0]?.payload?.mime, recorder.mimeType, 'AudioStart must include recorder MIME');

  const chunkEvents = transportEvents.filter((ev) => ev.kind === 'chunk');
  assert.equal(chunkEvents.length, 2, 'both pre-roll and live chunks should be sent');
  const texts = [];
  for (const ev of chunkEvents) {
    texts.push(await ev.payload.text());
  }
  assert.deepEqual(texts, ['pre', 'live'], 'pre-roll chunk must precede the live chunk');
  assert.equal(chunkEvents[0].payload.type, 'audio/webm; codecs=opus', 'pre-roll chunk should use WebM/Opus');

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
