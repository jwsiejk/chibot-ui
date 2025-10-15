import { test } from 'node:test';
import assert from 'node:assert/strict';

import { onTtsStart, onTtsEnd, registerTtsEventListener } from '../../static/js/voice/tts/TtsHandlers.js';

const listenerMap = new Map();

const windowStub = globalThis.window || {};
windowStub.ADVANCED_LOGGING_ENABLED = false;
windowStub.addEventListener = function addEventListener(type, handler) {
  if (!listenerMap.has(type)) {
    listenerMap.set(type, new Set());
  }
  listenerMap.get(type).add(handler);
};
windowStub.dispatchEvent = function dispatchEvent(event) {
  const listeners = listenerMap.get(event.type);
  if (!listeners) return true;
  for (const handler of Array.from(listeners)) {
    handler(event);
  }
  return true;
};
globalThis.window = windowStub;

function resetWindowListeners() {
  listenerMap.clear();
}

test('onTtsStart updates state for playing events', () => {
  const maskCalls = [];
  const state = {
    ttsPlaying: false,
    assistantReady: true,
    assistantPhase: 'init',
    postTtsHoldUntil: 0,
    ttsMask: { start: () => maskCalls.push('start'), end: () => {} },
    evidenceGate: { isOpen: () => false },
    eligibility: 'blocked_pregreet',
  };
  const ctx = {
    state,
    now: () => 1000,
    abortEvidenceGate: () => {},
    ttsIsPlaying: () => true,
    TurnState: { Speaking: 'SPEAKING', Ready: 'READY' },
    event: { detail: { state: 'playing' } },
  };

  const result = onTtsStart(ctx);

  assert.equal(result, true);
  assert.equal(state.ttsPlaying, true);
  assert.equal(state.assistantReady, false);
  assert.equal(state.assistantPhase, 'speaking');
  assert.equal(state.postTtsHoldUntil, 1600);
  assert.equal(state.eligibility, 'holdoff');
  assert.deepEqual(maskCalls, ['start']);
});

test('onTtsEnd resets playback state for ended events', () => {
  let cleared = 0;
  const maskArgs = [];
  const state = {
    ttsPlaying: true,
    postTtsHoldUntil: 500,
    sessionSnrStd: 2,
    assistantReady: false,
    assistantPhase: 'speaking',
    lastAssistantReadyAt: 0,
    eligibility: 'holdoff',
    ttsMask: { start: () => {}, end: (args) => maskArgs.push(args) },
  };
  const ctx = {
    state,
    now: () => 2000,
    clearPostTtsHoldTimer: () => { cleared += 1; },
    TurnState: { Ready: 'READY', Speaking: 'SPEAKING' },
    event: { detail: { state: 'ended' } },
  };

  const result = onTtsEnd(ctx);

  assert.equal(result, true);
  assert.equal(cleared, 1);
  assert.equal(state.ttsPlaying, false);
  assert.equal(state.postTtsHoldUntil, 0);
  assert.equal(state.assistantReady, true);
  assert.equal(state.assistantPhase, 'ready');
  assert.equal(state.lastAssistantReadyAt, 2000);
  assert.equal(state.eligibility, 'eligible');
  assert.deepEqual(maskArgs, [{ decayMs: 750, snrBoost: 3 }]);
});

test('registerTtsEventListener reuses context between handlers', () => {
  resetWindowListeners();
  const contexts = [];

  registerTtsEventListener({
    createContext: () => ({ markers: [] }),
    onTtsStart: (ctx) => {
      contexts.push({ phase: 'start', ctx });
      ctx.markers.push('start');
      return false;
    },
    onTtsEnd: (ctx) => {
      contexts.push({ phase: 'end', ctx });
      ctx.markers.push('end');
      return true;
    },
  });

  window.dispatchEvent({ type: 'chip-tts', detail: { state: 'ended' } });

  assert.equal(contexts.length, 2);
  assert.strictEqual(contexts[0].ctx, contexts[1].ctx);
  assert.deepEqual(contexts[1].ctx.markers, ['start', 'end']);
});
