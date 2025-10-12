import test from 'node:test';
import assert from 'node:assert/strict';

import {
  setupVoiceTestEnv,
  windowStub,
  fakeStream,
} from './helpers/voice-test-setup.mjs';

await setupVoiceTestEnv();

const voice = await import(new URL('../../static/js/voice.js', import.meta.url));
const hooks = voice.__TEST_ONLY__;
const audio = await import(new URL('../../static/js/audio.js', import.meta.url));

test('correlated echo frames are suppressed before VAD start', async (t) => {
  windowStub.__askchip_config.vad = {
    pollMs: 10,
    minSpeechMs: 0,
    minSilenceMs: 0,
    ECHO_SUPPRESS_DB: 15,
  };

  let echoDb = -18;
  voice.__TEST_ONLY__.setEchoSignatureOverride(() => ({
    rmsDb: echoDb,
    timestamp: performance.now(),
    mfcc: [1, 2, 3],
  }));

  const events = [];
  const handler = (event) => events.push(event.detail);
  windowStub.addEventListener('askchip-voice-lifecycle', handler);

  await voice.armVAD();
  t.after(() => {
    voice.disarmVAD();
    hooks.state.stream = fakeStream;
    voice.__TEST_ONLY__.setEchoSignatureOverride(null);
    windowStub.removeEventListener('askchip-voice-lifecycle', handler);
    audio.audioTeardown();
  });

  const analyser = hooks.state.analyser;
  analyser.setRmsDb(-20);

  await new Promise((resolve) => setTimeout(resolve, 60));

  const suppressed = events.filter((e) => e?.event === 'vad_echo_suppressed');
  const starts = events.filter((e) => e?.event === 'vad_speech_start');

  assert.ok(suppressed.length > 0, 'suppressed frames should be logged');
  assert.equal(starts.length, 0, 'speech should not start when echo dominates');
});

test('uncorrelated mic frames surpass echo gap and trigger speech', async (t) => {
  windowStub.__askchip_config.vad = {
    pollMs: 10,
    minSpeechMs: 0,
    minSilenceMs: 0,
    ECHO_SUPPRESS_DB: 12,
  };

  let echoDb = -60;
  voice.__TEST_ONLY__.setEchoSignatureOverride(() => ({
    rmsDb: echoDb,
    timestamp: performance.now(),
    mfcc: [0.1],
  }));

  const events = [];
  const handler = (event) => events.push(event.detail);
  windowStub.addEventListener('askchip-voice-lifecycle', handler);

  await voice.armVAD();
  t.after(() => {
    voice.disarmVAD();
    hooks.state.stream = fakeStream;
    voice.__TEST_ONLY__.setEchoSignatureOverride(null);
    windowStub.removeEventListener('askchip-voice-lifecycle', handler);
    audio.audioTeardown();
  });

  const analyser = hooks.state.analyser;
  analyser.setRmsDb(-20);

  await new Promise((resolve) => setTimeout(resolve, 80));

  const starts = events.filter((e) => e?.event === 'vad_speech_start');
  assert.ok(starts.length > 0, 'speech start should be logged when mic dominates echo');

  const suppressed = events.filter((e) => e?.event === 'vad_echo_suppressed');
  assert.equal(suppressed.length, 0, 'no suppression should be logged when gap exceeds threshold');
});
