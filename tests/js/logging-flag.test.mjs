import test from 'node:test';
import assert from 'node:assert/strict';

class TestCustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
}

const logs = [];
const consoleStub = {
  log: (...args) => logs.push(['log', args]),
  info: (...args) => logs.push(['info', args]),
  warn: (...args) => logs.push(['warn', args]),
  error: (...args) => logs.push(['error', args]),
  debug: (...args) => logs.push(['debug', args]),
};

function resetLogs() {
  logs.length = 0;
}

const listenerMap = new Map();

const windowStub = {
  __askchip_config: { logging: { enabled: false } },
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

Object.defineProperty(globalThis, 'window', { value: windowStub, configurable: true });
Object.defineProperty(globalThis, 'console', { value: consoleStub, configurable: true });
Object.defineProperty(globalThis, 'CustomEvent', { value: TestCustomEvent, configurable: true });

Object.defineProperty(globalThis, 'document', {
  value: {
    readyState: 'complete',
    querySelector: () => null,
    getElementById: () => null,
    addEventListener: () => {},
  },
  configurable: true,
});

Object.defineProperty(globalThis, 'performance', {
  value: { now: () => Date.now() },
  configurable: true,
});

Object.defineProperty(globalThis, 'navigator', {
  value: { mediaDevices: {} },
  configurable: true,
});

Object.defineProperty(globalThis, '__TEST_WS_HOOKS', {
  value: {},
  configurable: true,
});

const wsStubModule = await import('./ws_stub.mjs');
Object.defineProperty(globalThis, '__TEST_WS_MODULE', {
  value: wsStubModule,
  configurable: true,
});

const loggingMod = await import(new URL('../../static/js/util/logging.js', import.meta.url));
const appMod = await import(new URL('../../static/js/app.js', import.meta.url));
const wsMod = await import(new URL('../../static/js/ws.js', import.meta.url));
const bootstrapMod = await import(new URL('../../static/js/bootstrap.js', import.meta.url));

function assertNoLogs(message) {
  assert.equal(logs.length, 0, message);
}

test('advanced logging flag suppresses module output when disabled', async () => {
  windowStub.__askchip_config.logging.enabled = false;
  resetLogs();

  loggingMod.logIfEnabled(() => logs.push(['direct', []]));
  assertNoLogs('logIfEnabled should not execute callback when disabled');

  appMod.__TEST_ONLY__.appLog('info', 'ui message');
  wsMod.__TEST_ONLY__.wsLog('warn', 'ws message');
  bootstrapMod.__TEST_ONLY__.console('info', 'bootstrap message');
  assertNoLogs('module log helpers should not emit when disabled');

  windowStub.__askchip_config.logging.enabled = true;
  resetLogs();

  loggingMod.logIfEnabled(() => logs.push(['direct', []]));
  appMod.__TEST_ONLY__.appLog('info', 'ui message');
  wsMod.__TEST_ONLY__.wsLog('warn', 'ws message');
  bootstrapMod.__TEST_ONLY__.console('info', 'bootstrap message');

  assert.equal(logs.length, 4, 'all logging pathways should emit when enabled');
  assert.deepEqual(
    logs.map(([level]) => level),
    ['direct', 'info', 'warn', 'info'],
  );
});
