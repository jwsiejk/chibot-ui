import { VAD } from '../vad.js';
import { dbToRms } from '../core/index.js';

const activeLoops = new WeakMap();

export function startVadLoop(ctx, onFrame) {
  if (!ctx || typeof onFrame !== 'function') {
    return null;
  }

  const previous = activeLoops.get(ctx);
  if (previous?.vad && typeof previous.vad.stop === 'function') {
    try { previous.vad.stop(); } catch {}
  }

  const config = onFrame();
  if (!config || typeof config !== 'object') {
    activeLoops.delete(ctx);
    return null;
  }

  const {
    analyser = ctx.analyser,
    cfg = {},
    pollMs: pollOverride,
    onSpeechStart,
    onSpeechEnd,
    ttsIsPlaying,
  } = config;

  const pollMs = Number.isFinite(pollOverride)
    ? pollOverride
    : (Number.isFinite(cfg.pollMs) ? cfg.pollMs : 33);

  const options = {
    pollMs,
    minSpeechMs: cfg.minSpeechMs ?? 280,
    minSilenceMs: cfg.minSilenceMs ?? 300,
    cooldownMs: cfg.cooldownMs ?? 380,
    startDbOffset: cfg.startDbOffset ?? 10,
    stopDbOffset: cfg.stopDbOffset ?? 6,
    minStartDb: cfg.minStartDb ?? -65,
    minStopDb: cfg.minStopDb ?? -70,
    echoBoostStartDb: cfg.echoBoostStartDb ?? 8,
    echoBoostStopDb: cfg.echoBoostStopDb ?? 6,
    noiseFloorAlpha: cfg.noiseFloorAlpha ?? 0.05,
    noiseFloorRiseAlpha: cfg.noiseFloorRiseAlpha ?? 0.01,
    noiseFloorGuardDb: cfg.noiseFloorGuardDb ?? 3,
    noiseFloorHangMs: cfg.noiseFloorHangMs ?? 600,
    initialNoiseFloorDb: cfg.initialNoiseFloorDb,
    startRms: cfg.startRms,
    stopRms: cfg.stopRms,
    echoStateFn: () => {
      if (typeof ttsIsPlaying === 'function') {
        try { return !!ttsIsPlaying(); } catch { return false; }
      }
      return false;
    },
  };

  const callbacks = {};
  if (typeof onSpeechStart === 'function') {
    callbacks.onSpeechStart = onSpeechStart;
  }
  if (typeof onSpeechEnd === 'function') {
    callbacks.onSpeechEnd = onSpeechEnd;
  }

  const vad = new VAD(analyser, options, callbacks);
  vad.start();

  const entry = { vad, pollMs };
  activeLoops.set(ctx, entry);
  return entry;
}

export function stopVadLoop(ctx) {
  if (!ctx) {
    return;
  }
  const entry = activeLoops.get(ctx);
  if (entry?.vad && typeof entry.vad.stop === 'function') {
    try { entry.vad.stop(); } catch {}
  }
  activeLoops.delete(ctx);
}

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
