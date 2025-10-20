import test from 'node:test';
import assert from 'node:assert/strict';

const listeners = new Map();

function setupDom() {
  const documentStub = {
    readyState: 'complete',
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    removeEventListener(type) {
      listeners.delete(type);
    },
    getElementById() {
      return null;
    },
    querySelector() {
      return null;
    },
    createElement(tag) {
      return {
        tagName: tag.toUpperCase(),
        style: {},
        href: '',
        download: '',
        click() {},
        remove() {},
      };
    },
    body: {
      appendChild() {},
    },
  };

  const windowStub = {
    location: { origin: 'http://localhost', hash: '' },
    history: { replaceState() {} },
    addEventListener() {},
    removeEventListener() {},
    scrollTo() {},
  };
  documentStub.defaultView = windowStub;

  Object.defineProperty(globalThis, 'document', {
    value: documentStub,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(globalThis, 'window', {
    value: windowStub,
    configurable: true,
    writable: true,
  });

  Object.defineProperty(windowStub, 'document', {
    value: documentStub,
    configurable: true,
    writable: true,
  });

  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async () => {} } },
    configurable: true,
  });

  Object.defineProperty(globalThis, 'CustomEvent', {
    value: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    configurable: true,
  });

  globalThis.requestAnimationFrame = (cb) => {
    cb();
    return 1;
  };

}

setupDom();

const modulePromise = import(new URL('../../static/js/admin_flow.js', import.meta.url));

function prepareFlow(flow) {
  flow.setFetch(async () => ({
    ok: true,
    json: async () => ({ events: [], hints: [], next_since_ms: 0 }),
    blob: async () => ({}),
  }));
  flow.setSessionForTests('');
  flow.setLevelsForTests(['flow', 'transition', 'debug']);
  flow.state.events = new Map();
  flow.state.expanded = new Set();
  flow.state.bookmarks = [];
  flow.state.pollTimer = null;
  flow.state.hints = [];
  flow.state.hintMap = new Map();
  flow.state.grouping = 'chronological';
  flow.state.live = false;
  flow.state.pendingExpandedRestore = null;
  flow.state.sessionId = '';

  const makeButton = (valueAttr, getValue) => ({
    getAttribute: () => getValue ?? valueAttr,
    classList: { toggle() {} },
  });

  flow.els.tailState = { textContent: '' };
  flow.els.tailToggle = { textContent: '' };
  flow.els.sessionHint = { textContent: '' };
  flow.els.timeline = { innerHTML: '', querySelectorAll: () => [], querySelector: () => null };
  flow.els.drawer = { setAttribute() {} };
  flow.els.drawerTitle = { textContent: '' };
  flow.els.drawerMeta = { innerHTML: '' };
  flow.els.drawerJson = { textContent: '' };
  flow.els.drawerRelated = { innerHTML: '' };
  flow.els.hints = { innerHTML: '' };
  flow.els.filterChips = { innerHTML: '' };
  flow.els.levelContainer = {
    querySelectorAll: () => [
      { getAttribute: () => 'flow', classList: { toggle() {} } },
      { getAttribute: () => 'transition', classList: { toggle() {} } },
      { getAttribute: () => 'debug', classList: { toggle() {} } },
      { getAttribute: () => 'raw', classList: { toggle() {} } },
    ],
  };
  flow.els.groupContainer = {
    querySelectorAll: () => [
      { getAttribute: () => 'phase', classList: { toggle() {} } },
      { getAttribute: () => 'turn', classList: { toggle() {} } },
      { getAttribute: () => 'chronological', classList: { toggle() {} } },
    ],
  };
}

test('tail goLive/pause toggles live state and schedules poll', async () => {
  const mod = await modulePromise;
  const flow = mod.__TEST_ONLY__;
  prepareFlow(flow);
  flow.setSessionForTests('sess-tail');

  const timers = [];
  const cleared = [];
  globalThis.setTimeout = (fn, delay) => {
    timers.push({ fn, delay });
    return timers.length;
  };
  globalThis.clearTimeout = (id) => {
    cleared.push(id);
  };
  const originalRandom = Math.random;
  Math.random = () => 0;

  flow.goLive(false);
  assert.equal(flow.state.live, true);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 750);

  flow.pauseLive();
  assert.equal(flow.state.live, false);
  assert.deepEqual(cleared, [1]);

  Math.random = originalRandom;
});

test('level toggles and grouping update state and history', async () => {
  const mod = await modulePromise;
  const flow = mod.__TEST_ONLY__;
  prepareFlow(flow);
  flow.setSessionForTests('sess-toggle');

  const historyCalls = [];
  window.history.replaceState = (...args) => {
    historyCalls.push(args);
  };

  let fetchCalls = 0;
  flow.setFetch(async () => {
    fetchCalls += 1;
    return { ok: true, json: async () => ({ events: [], hints: [], next_since_ms: 0 }) };
  });

  assert.equal(flow.state.levels.has('debug'), true);
  flow.toggleLevel('debug');
  assert.equal(flow.state.levels.has('debug'), false);
  flow.toggleLevel('debug');
  assert.equal(flow.state.levels.has('debug'), true);

  flow.setGrouping('phase');
  assert.equal(flow.state.grouping, 'phase');
  assert.ok(historyCalls.length >= 1);
  assert.ok(fetchCalls >= 1);
});

test('download export requests NDJSON with expected params', async () => {
  const mod = await modulePromise;
  const flow = mod.__TEST_ONLY__;
  prepareFlow(flow);
  flow.setSessionForTests('sess-export');
  flow.setLevelsForTests(['flow', 'debug']);

  const requests = [];
  flow.setFetch(async (url, opts = {}) => {
    requests.push({ url, opts });
    return { ok: true, blob: async () => ({}) };
  });

  let clicked = false;
  let removed = false;
  document.createElement = () => ({
    href: '',
    download: '',
    click() {
      clicked = true;
    },
    remove() {
      removed = true;
    },
    style: {},
  });

  const urls = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => {
    urls.push(blob);
    return 'blob://export';
  };
  URL.revokeObjectURL = (href) => {
    urls.push(href);
  };

  flow.downloadExport({ redacted: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /redacted=1/);
  assert.match(requests[0].url, /levels=flow%2Cdebug/);
  assert.equal(clicked, true);
  assert.equal(removed, true);
  assert.equal(urls[1], 'blob://export');

  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});

test('handoff posts session payload and triggers download', async () => {
  const mod = await modulePromise;
  const flow = mod.__TEST_ONLY__;
  prepareFlow(flow);
  flow.setSessionForTests('sess-hand');
  flow.setLevelsForTests(['flow', 'transition', 'debug']);

  const calls = [];
  flow.setFetch(async (url, opts = {}) => {
    calls.push({ url, opts });
    return { ok: true, blob: async () => ({}), headers: new Map() };
  });

  let clicked = false;
  let removed = false;
  document.createElement = () => ({
    href: '',
    download: '',
    click() {
      clicked = true;
    },
    remove() {
      removed = true;
    },
    style: {},
  });

  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = () => 'blob://handoff';
  URL.revokeObjectURL = () => {};

  flow.handoffToChatGPT();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/flow/handoff');
  const payload = JSON.parse(calls[0].opts.body);
  assert.equal(payload.session_id, 'sess-hand');
  assert.deepEqual(new Set(payload.levels), new Set(['flow', 'transition', 'debug']));
  assert.ok(payload.prompt.includes('Analyze the redacted conversational flow'));
  assert.equal(clicked, true);
  assert.equal(removed, true);

  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});
