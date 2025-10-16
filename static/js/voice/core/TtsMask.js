let cachedTimeOrigin = null;

const resolveTimeOrigin = (perfNow) => {
  if (cachedTimeOrigin !== null) {
    return cachedTimeOrigin;
  }
  try {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
      if (typeof performance.timeOrigin === 'number') {
        cachedTimeOrigin = performance.timeOrigin;
      } else {
        const referenceNow = Number.isFinite(perfNow) ? perfNow : performance.now();
        cachedTimeOrigin = Date.now() - referenceNow;
      }
      return cachedTimeOrigin;
    }
  } catch {}
  cachedTimeOrigin = 0;
  return cachedTimeOrigin;
};

export const nowMs = () => {
  try {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
      const perfNow = performance.now();
      return resolveTimeOrigin(perfNow) + perfNow;
    }
  } catch {}
  return Date.now();
};

const clampMs = (value) => {
  if (!Number.isFinite(value) || value <= 0) return 0;
  return value;
};

export class TtsMask {
  constructor() {
    this._active = false;
    this._decayUntil = 0;
    this._boostDb = 0;
  }

  start() {
    this._active = true;
    this._decayUntil = 0;
    this._boostDb = 0;
  }

  end({ decayMs, snrBoost = 0 } = {}) {
    this._active = false;
    const duration = clampMs(decayMs);
    if (duration > 0) {
      this._decayUntil = nowMs() + duration;
      if (Number.isFinite(snrBoost) && snrBoost > 0) {
        this._boostDb = snrBoost;
      } else if (!Number.isFinite(this._boostDb) || this._boostDb < 0) {
        this._boostDb = 0;
      }
    } else {
      this._decayUntil = 0;
      this._boostDb = 0;
    }
  }

  isMasked(at = nowMs()) {
    if (this._active) {
      return true;
    }
    if (!this._decayUntil) {
      return false;
    }
    if (at >= this._decayUntil) {
      this._decayUntil = 0;
      this._boostDb = 0;
      return false;
    }
    return true;
  }

  snrBoost(at = nowMs()) {
    return this.isMasked(at) ? Math.max(0, this._boostDb || 0) : 0;
  }

  decayUntil() {
    return this._decayUntil;
  }

  clear() {
    this._active = false;
    this._decayUntil = 0;
    this._boostDb = 0;
  }
}
