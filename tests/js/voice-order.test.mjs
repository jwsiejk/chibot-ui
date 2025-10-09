import test from 'node:test';
import assert from 'node:assert/strict';

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

Object.defineProperty(globalThis, 'navigator', {
  value: { mediaDevices: {} },
  configurable: true,
  writable: true,
});

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

test('final audio chunk drains before CloseStream and recorder can re-arm', async () => {
  const voice = await import('../../static/js/voice.js');
  const hooks = voice.__TEST_ONLY__;

  hooks.state.stream = { active: true };
  hooks.state.turnOpen = false;
  hooks.state.turnClosePromise = null;
  hooks.state.chunkSendPromise = Promise.resolve();

  const started = hooks.startRecorder();
  assert.equal(started, true, 'recorder should start with fake stream');

  const recorder = FakeRecorder.instances.at(-1);
  assert.ok(recorder, 'fake recorder instance should exist');
  assert.equal(recorder.state, 'recording');
  assert.ok(typeof recorder.onstop === 'function', 'onstop handler should be assigned');

  const events = [];
  let closeResolve;
  const closeSeen = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('CloseStream not observed before timeout')), 100);
    closeResolve = () => {
      clearTimeout(timer);
      resolve();
    };
  });

  globalThis.__TEST_WS_HOOKS = {
    onSendCloseStream: () => {
      events.push('closeStream');
      if (closeResolve) closeResolve();
    },
  };

  let chunkResolve;
  hooks.state.chunkSendPromise = new Promise((resolve) => {
    chunkResolve = resolve;
  });

  const handler = hooks.state.wsListener;
  assert.ok(typeof handler === 'function', 'WS listener should be registered');

  const handlerPromise = handler({ detail: { type: 'UtteranceEnd' } });
  assert.equal(recorder.stopCalled, true, 'server final should stop recorder');

  const onstopPromise = recorder.onstop();
  assert.ok(onstopPromise instanceof Promise, 'onstop handler should return a promise');

  events.push('chunkResolved');
  chunkResolve();

  await handlerPromise;
  await onstopPromise;
  await closeSeen;

  assert.deepEqual(events, ['chunkResolved', 'closeStream'], 'CloseStream must follow the final chunk resolution');
  assert.equal(hooks.state.turnOpen, false, 'turn flag should reset after CloseStream');
  assert.equal(hooks.state.turnClosePromise, null, 'turn close promise should clear after CloseStream');
  if (hooks.state.turnTimer) {
    clearTimeout(hooks.state.turnTimer);
    hooks.state.turnTimer = null;
  }

  FakeRecorder.instances = [];
  hooks.state.rec = null;
  hooks.state.chunkSendPromise = Promise.resolve();

  const restarted = hooks.startRecorder();
  assert.equal(restarted, true, 'recorder should re-arm after CloseStream');

  const restartedRecorder = FakeRecorder.instances.at(-1);
  globalThis.__TEST_WS_HOOKS = {};
  hooks.stopRecorder({ reason: 'test_cleanup' });
  if (restartedRecorder && typeof restartedRecorder.onstop === 'function') {
    await restartedRecorder.onstop();
  }
  if (hooks.state.turnTimer) {
    clearTimeout(hooks.state.turnTimer);
    hooks.state.turnTimer = null;
  }
});
