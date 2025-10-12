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

test('VAD applies injected config thresholds and boosts', async (t) => {
  windowStub.__askchip_config.vad = {
    baseThresholdDb: 11,
    exitThresholdDb: 7,
    ttsBoostDb: 5,
    minSpeechMs: 420,
    ECHO_SUPPRESS_DB: 18,
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
  assert.equal(vad.opts.echoSuppressDb, 18, 'echo suppression gap should respect config override');

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
  assert.equal(vad.opts.echoSuppressDb, 15, 'default echo suppression gap should be 15 dB');
  voice.disarmVAD();
});
