const EPSILON = 1e-12;

const clampAlpha = (value) => {
  if (!Number.isFinite(value)) return 0.2;
  return Math.min(1, Math.max(0, value));
};

const computeRms = (samples) => {
  if (!samples || typeof samples.length !== 'number') return null;
  let sum = 0;
  const length = samples.length;
  for (let i = 0; i < length; i += 1) {
    const v = samples[i];
    sum += v * v;
  }
  if (length === 0) return null;
  return Math.sqrt(sum / length);
};

export const rmsToDb = (rms) => 20 * Math.log10(Math.max(EPSILON, rms));
export const dbToRms = (db) => Math.pow(10, db / 20);

export class NoiseModel {
  constructor({ alpha } = {}) {
    this.alpha = clampAlpha(alpha);
    this._primed = false;
    this._floorRms = null;
  }

  prime(samples) {
    const rms = computeRms(samples);
    if (!Number.isFinite(rms) || rms <= 0) {
      return null;
    }
    this._floorRms = rms;
    this._primed = true;
    return rmsToDb(rms);
  }

  observeSilence({ energy } = {}) {
    if (energy == null) return;
    let rms = energy;
    if (ArrayBuffer.isView(energy)) {
      rms = computeRms(energy);
    } else if (energy instanceof Float32Array) {
      rms = computeRms(energy);
    } else if (typeof energy === 'object' && Array.isArray(energy)) {
      rms = computeRms(energy);
    }
    if (!Number.isFinite(rms) || rms <= 0) {
      return;
    }
    if (!this._primed || !Number.isFinite(this._floorRms) || this._floorRms <= 0) {
      this._floorRms = rms;
      this._primed = true;
      return;
    }
    const alpha = this.alpha;
    this._floorRms = (1 - alpha) * this._floorRms + alpha * rms;
  }

  getFloor() {
    if (!this._primed || !Number.isFinite(this._floorRms) || this._floorRms <= 0) {
      return null;
    }
    return rmsToDb(this._floorRms);
  }

  snr(currentRms) {
    let rms = currentRms;
    if (ArrayBuffer.isView(currentRms)) {
      rms = computeRms(currentRms);
    } else if (currentRms instanceof Float32Array) {
      rms = computeRms(currentRms);
    }
    if (!Number.isFinite(rms) || rms <= 0) return null;
    if (!this._primed || !Number.isFinite(this._floorRms) || this._floorRms <= 0) return null;
    const ratio = rms / this._floorRms;
    return 20 * Math.log10(Math.max(EPSILON, ratio));
  }
}

export function createNoiseModel(opts = {}) {
  return new NoiseModel(opts);
}
