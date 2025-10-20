import test from 'node:test';
import assert from 'node:assert/strict';

import {
  setupVoiceTestEnv,
  windowStub,
  fakeStream,
} from './helpers/voice-test-setup.mjs';

await setupVoiceTestEnv();

globalThis.ADVANCED_LOGGING_ENABLED = true;
windowStub.ADVANCED_LOGGING_ENABLED = true;

const voice = await import(new URL('../../static/js/voice.js', import.meta.url));
const audioModule = await import(new URL('../../static/js/audio.js', import.meta.url));
const audioTeardown = audioModule.audioTeardown ?? (() => {});
const hooks = voice.__TEST_ONLY__;

const policyBusModule = await import(new URL('../../static/js/voice/policy/PolicyBus.js', import.meta.url));
const PolicyBus = policyBusModule.default ?? policyBusModule.PolicyBus;
const policyModule = await import(new URL('../../static/js/voice/policy/InteractionPolicy.js', import.meta.url));
const { manualOnlyDuringTtsPolicy, autoVadReadyPolicy } = policyModule;
const { TurnState } = await import(new URL('../../static/js/voice/core/TurnState.js', import.meta.url));
const { guardBargeInDispatch } = await import(new URL('../../static/js/voice/guards/VadGuard.js', import.meta.url));

const dispatchWsFrame = (frame) => {
  const event = new windowStub.CustomEvent('askchip-ws', { detail: frame });
  windowStub.dispatchEvent(event);
};

const clearBreadcrumbs = () => {
  if (Array.isArray(window.__voice_breadcrumbs)) {
    window.__voice_breadcrumbs.length = 0;
  } else {
    window.__voice_breadcrumbs = [];
  }
};

const breadcrumbNames = () => (Array.isArray(window.__voice_breadcrumbs)
  ? window.__voice_breadcrumbs.map((entry) => entry.name)
  : []);

const getCtx = () => hooks.getCtx?.();

const flushMicrotasks = () => new Promise((resolve) => setTimeout(resolve, 0));

const clearTimer = (handle) => {
  if (!handle) return;
  try { clearTimeout(handle); } catch {}
  try { clearInterval(handle); } catch {}
};

function cleanupVoiceRuntime() {
  try { voice.forceBargeInEnd({ reason: 'test_cleanup', pttHeld: false }); } catch {}
  try { voice.disarmVAD(); } catch {}
  try { PolicyBus.clear?.(); } catch {}

  const ctx = getCtx();
  if (!ctx) {
    return;
  }

  const { state = {}, audio = {}, transport = {} } = ctx;

  try { ctx.controllers?.recorder?.stop?.({ reason: 'test_cleanup', emit: false }); } catch {}
  try { ctx.controllers?.asr?.stopIfIdle?.({ reason: 'test_cleanup', emit: false }); } catch {}
  try { ctx.controllers?.asr?.teardownTransport?.(); } catch {}

  if (audio.vad?.stop) {
    try { audio.vad.stop(); } catch {}
  }
  audio.vad = null;

  if (audio.recorder) {
    try { audio.recorder.stop(); } catch {}
    audio.recorder = null;
  }

  clearTimer(audio.rmsSampleTimer);
  audio.rmsSampleTimer = null;

  clearTimer(state.ttsHeartbeatTimer);
  state.ttsHeartbeatTimer = null;

  clearTimer(state.ttsRearmTimer);
  state.ttsRearmTimer = null;

  clearTimer(state.dualVadCloseTimer);
  state.dualVadCloseTimer = null;

  clearTimer(ctx.maskLogTimer);
  ctx.maskLogTimer = null;

  clearTimer(transport.safetyTimer);
  transport.safetyTimer = null;

  audio.stream = fakeStream;
  if (typeof audio?.context?.close === 'function') {
    try { audio.context.close(); } catch {}
  }
  audio.context = null;
  audio.source = null;
  audio.analyser = null;
  audio.highpass = null;
  audio.encoderDestination = null;
  audio.encoderStream = null;
  audio.lastTimecode = null;

  try { ctx.shadowBuffer?.clear?.(); } catch {}
  try { ctx.evidenceGate?.reset?.('test_cleanup'); } catch {}

  try { audioTeardown(); } catch {}
}

async function armForTest() {
  await voice.armVAD();
  const ctx = getCtx();
  if (!ctx) {
    throw new Error('voice runtime context not initialised');
  }
  return ctx;
}

test('AT1_ptt_blocks_auto_vad_during_tts', async (t) => {
  clearBreadcrumbs();

  const ctx = await armForTest();
  PolicyBus.setPolicy(manualOnlyDuringTtsPolicy());
  clearBreadcrumbs();
  t.after(cleanupVoiceRuntime);

  ctx.state.ttsPlaying = false;
  ctx.state.manualGate = false;

  const allowed = guardBargeInDispatch('client_vad');
  await flushMicrotasks();

  const names = breadcrumbNames();
  assert.equal(allowed, false, 'policy guard should block auto VAD while manual mode is active');
  assert.ok(names.includes('barge_in:blocked'), 'guard should emit a blocked barge breadcrumb');
  assert.equal(ctx.state.turnOpen, false, 'turn should remain closed after blocked auto VAD');
});

test('AT2_ptt_interrupts_and_opens_turn', async (t) => {
  clearBreadcrumbs();

  const ctx = await armForTest();
  PolicyBus.setPolicy(manualOnlyDuringTtsPolicy());
  t.after(cleanupVoiceRuntime);

  ctx.state.ttsPlaying = true;
  ctx.state.manualGate = false;

  voice.forceBargeInStart({ source: 'test_ptt' });
  await flushMicrotasks();

  const names = breadcrumbNames();
  assert.ok(names.includes('ptt_open'), 'PTT start should emit client ptt_open event');
  assert.ok(names.includes('recorder_start'), 'Recorder should start when PTT engages');
  assert.ok(names.includes('asr_start'), 'ASR should start when PTT engages');

  ctx.controllers.asr.notifyPartial({ confidence: 0.82, transcript: 'hello' });
  await flushMicrotasks();
  const updatedNames = breadcrumbNames();
  assert.ok(updatedNames.includes('asr_partial'), 'ASR partials should flow after PTT engages');
  assert.equal(ctx.state.manualGate, true, 'manual gate should remain engaged while holding PTT');

  voice.forceBargeInEnd({ reason: 'test_release' });
  await flushMicrotasks();
});

test('AT3_auto_vad_idle_commit', async (t) => {
  clearBreadcrumbs();

  PolicyBus.setPolicy(autoVadReadyPolicy());
  assert.equal(PolicyBus.getPolicy()?.mode, autoVadReadyPolicy().mode, 'auto VAD policy should be active');
  const originalGetPolicy = PolicyBus.getPolicy?.bind(PolicyBus);
  PolicyBus.getPolicy = () => autoVadReadyPolicy();
  const ctx = await armForTest();
  PolicyBus.setPolicy(autoVadReadyPolicy());
  t.after(cleanupVoiceRuntime);
  t.after(() => {
    if (originalGetPolicy) {
      PolicyBus.getPolicy = originalGetPolicy;
    }
  });

  ctx.state.ttsPlaying = false;
  ctx.state.manualGate = false;
  voice.forceBargeInEnd({ reason: 'test_auto_rearm', pttHeld: false });
  ctx.ttsMask?.clear?.();
  ctx.state.ttsHoldUntilMs = 0;
  voice.forceBargeInEnd({ reason: 'test_auto_rearm_final', pttHeld: false });
  await new Promise((resolve) => setTimeout(resolve, 20));

  PolicyBus.setPolicy(autoVadReadyPolicy());
  const vad = ctx.audio?.vad;
  assert.ok(vad, `VAD should be initialised for auto mode (pending=${JSON.stringify(ctx.state?.pendingVadOpts)}, suppressed=${ctx.state?.vadSuppressedForTts}, shouldRearm=${ctx.state?.vadShouldRearmAfterTts})`);
  assert.equal(typeof vad?.cbs?.onSpeechStart, 'function', 'VAD speech start callback should exist');
  vad.cbs.onSpeechStart?.({ snrDb: 14 });
  await flushMicrotasks();

  dispatchWsFrame({
    type: 'result',
    channel: {
      alternatives: [{ confidence: 0.92 }],
      is_final: false,
    },
  });
  await flushMicrotasks();

  const names = breadcrumbNames();
  assert.ok(names.includes('asr_partial'), 'ASR partial should emit after auto VAD speech');

  ctx.state.preCommitASRFeed = true;
  ctx.state.turnState = TurnState.Confirming;
});

test('AT4_policy_pushes_on_transitions', async (t) => {
  const adminEvents = [];
  const previousLogger = globalThis.askchipLog;
  const previousWindowLogger = windowStub.askchipLog;
  globalThis.askchipLog = (payload) => {
    adminEvents.push({ ...payload });
  };
  windowStub.askchipLog = globalThis.askchipLog;

  t.after(() => {
    globalThis.askchipLog = previousLogger;
    windowStub.askchipLog = previousWindowLogger;
    cleanupVoiceRuntime();
  });

  dispatchWsFrame({ type: 'policy.interaction', policy: manualOnlyDuringTtsPolicy() });
  await flushMicrotasks();
  assert.ok(adminEvents.some((evt) => evt.event === 'policy:applied'), 'policy frame should emit admin log entry');

  adminEvents.length = 0;
  const ctx = await armForTest();
  PolicyBus.setPolicy(autoVadReadyPolicy());
  voice.forceBargeInStart({ source: 'policy_ptt' });
  voice.forceBargeInEnd({ reason: 'policy_ptt_release' });
  dispatchWsFrame({ type: 'policy.interaction', policy: autoVadReadyPolicy() });
  await flushMicrotasks();

  const appliedModes = adminEvents
    .filter((evt) => evt.event === 'policy:applied')
    .map((evt) => evt.mode);
  assert.ok(appliedModes.includes(autoVadReadyPolicy().mode), 'policy updates should push after transitions');
});
