import { dbToRms } from '../core/index.js';

export function updateSessionNoise(state, detail = null) {
  if (!state || !detail || typeof detail !== 'object') {
    return;
  }
  const noiseDb = Number.isFinite(detail.noiseFloorDb) ? detail.noiseFloorDb : null;
  if (Number.isFinite(noiseDb)) {
    if (state.noiseModel) {
      state.noiseModel.observeSilence({ energy: dbToRms(noiseDb) });
      const floorDb = state.noiseModel.getFloor();
      if (Number.isFinite(floorDb)) {
        state.sessionNoiseFloorDb = floorDb;
      }
    } else if (!Number.isFinite(state.sessionNoiseFloorDb)) {
      state.sessionNoiseFloorDb = noiseDb;
    } else {
      const alpha = 0.2;
      state.sessionNoiseFloorDb = (1 - alpha) * state.sessionNoiseFloorDb + alpha * noiseDb;
    }
  }

  const snr = Number.isFinite(detail.snrDb) ? detail.snrDb : null;
  if (Number.isFinite(snr)) {
    const count = (state.sessionSnrSamples || 0) + 1;
    const prevMean = state.sessionSnrMean || 0;
    const delta = snr - prevMean;
    const mean = prevMean + delta / count;
    const m2 = (state.sessionSnrM2 || 0) + delta * (snr - mean);
    state.sessionSnrSamples = count;
    state.sessionSnrMean = mean;
    state.sessionSnrM2 = m2;
    state.sessionSnrStd = count > 1 ? Math.sqrt(Math.max(0, m2 / (count - 1))) : state.sessionSnrStd;
  }
}

export function getEvidenceSnrRequirement(state, nowFn, baseSnr = 0) {
  let base = Number.isFinite(baseSnr) ? baseSnr : 0;
  if (state?.ttsPlaying) {
    base += 3;
  } else if (state?.ttsMask) {
    const now = typeof nowFn === 'function' ? nowFn() : Date.now();
    if (state.ttsMask.isMasked(now)) {
      const boost = state.ttsMask.snrBoost(now);
      base += Number.isFinite(boost) ? Math.max(0, boost) : 0;
    }
  }
  return base;
}

export function getShadowStats(state) {
  const stats = state?.shadowBuffer ? state.shadowBuffer.stats() : null;
  if (!stats) {
    return { count: 0, durationMs: 0, totalBytes: 0 };
  }
  return {
    count: Number.isFinite(stats.count) ? stats.count : (stats.count || 0),
    durationMs: Number.isFinite(stats.durationMs) ? stats.durationMs : 0,
    totalBytes: Number.isFinite(stats.totalBytes) ? stats.totalBytes : 0,
  };
}
