import test from 'node:test';
import assert from 'node:assert/strict';

const {
  getEvidenceSnrRequirement,
  getShadowStats,
  updateSessionNoise,
} = await import(new URL('../../static/js/voice/loops/VadLoop.js', import.meta.url));

test('getEvidenceSnrRequirement boosts for playback and mask state', () => {
  const baseSnr = 3.5;
  const maskState = {
    ttsPlaying: false,
    ttsMask: {
      isMasked: () => true,
      snrBoost: () => 2,
    },
  };
  const playingState = { ttsPlaying: true, ttsMask: null };

  assert.equal(
    getEvidenceSnrRequirement(maskState, () => 0, baseSnr),
    baseSnr + 2,
    'mask boost should add to base SNR',
  );
  assert.equal(
    getEvidenceSnrRequirement(playingState, () => 0, baseSnr),
    baseSnr + 3,
    'ttsPlaying should increase base SNR by 3 dB',
  );
});

test('getShadowStats returns safe defaults when buffer absent', () => {
  const emptyStats = getShadowStats({ shadowBuffer: null });
  assert.deepEqual(emptyStats, { count: 0, durationMs: 0, totalBytes: 0 });

  const bufferStats = getShadowStats({
    shadowBuffer: {
      stats: () => ({ count: undefined, durationMs: 15.2, totalBytes: NaN }),
    },
  });
  assert.equal(bufferStats.count, 0);
  assert.equal(bufferStats.durationMs, 15.2);
  assert.equal(bufferStats.totalBytes, 0);
});

test('updateSessionNoise tracks noise floor and SNR aggregates', () => {
  let observedEnergy = null;
  const state = {
    noiseModel: {
      observeSilence: ({ energy }) => { observedEnergy = energy; },
      getFloor: () => -48,
    },
    sessionNoiseFloorDb: null,
    sessionSnrSamples: 0,
    sessionSnrMean: 0,
    sessionSnrM2: 0,
    sessionSnrStd: 0,
  };

  updateSessionNoise(state, { noiseFloorDb: -42, snrDb: 4 });

  assert.ok(observedEnergy !== null, 'noise model should receive energy observations');
  assert.equal(state.sessionNoiseFloorDb, -48);
  assert.equal(state.sessionSnrSamples, 1);
  assert.equal(state.sessionSnrMean, 4);
  assert.equal(state.sessionSnrStd, 0);
});
