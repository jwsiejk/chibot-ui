const clampNumber = (value, fallback, { min = Number.NEGATIVE_INFINITY, max = Number.POSITIVE_INFINITY } = {}) => {
  const numeric = Number.isFinite(value) ? Number(value) : fallback;
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, numeric));
};

const clampDb = (value, fallback) => {
  if (!Number.isFinite(value)) return fallback;
  return Number(value);
};

const DEFAULT_FRAME_MS = 30;
const DEFAULT_OPEN_DB = -40;
const DEFAULT_CLOSE_DB = -46;
const DEFAULT_MIN_OPEN_MS = 200;
const MIN_OPEN_MS = 180;

export class HysteresisVAD {
  constructor({
    openDb = DEFAULT_OPEN_DB,
    closeDb = DEFAULT_CLOSE_DB,
    minOpenMs = DEFAULT_MIN_OPEN_MS,
    frameMs = DEFAULT_FRAME_MS,
    prerollMs = 160,
  } = {}) {
    const frame = clampNumber(frameMs, DEFAULT_FRAME_MS, { min: 1 });
    this.frameMs = frame;

    const coercedOpen = clampDb(openDb, DEFAULT_OPEN_DB);
    const coercedClose = clampDb(closeDb, DEFAULT_CLOSE_DB);
    this.openDb = coercedOpen;
    this.closeDb = coercedClose <= coercedOpen ? coercedClose : coercedOpen - 1;

    const requestedMinOpen = clampNumber(minOpenMs, DEFAULT_MIN_OPEN_MS, { min: MIN_OPEN_MS });
    this.minOpenMs = Math.max(MIN_OPEN_MS, requestedMinOpen);

    this.prerollMs = clampNumber(prerollMs, 160, { min: 0 });

    this.reset();
  }

  reset() {
    this._state = 'silence';
    this._riseAccumMs = 0;
    this._openElapsedMs = 0;
  }

  get state() {
    return this._state;
  }

  push(levelDb, deltaMs = this.frameMs) {
    const db = Number.isFinite(levelDb) ? Number(levelDb) : Number.NEGATIVE_INFINITY;
    const step = clampNumber(deltaMs, this.frameMs, { min: 0 });

    if (this._state === 'speech') {
      this._openElapsedMs += step;
      if (this._openElapsedMs < this.minOpenMs) {
        return 'speech';
      }
      if (db <= this.closeDb) {
        this.reset();
        return 'silence';
      }
      return 'speech';
    }

    if (db >= this.openDb) {
      this._riseAccumMs += step;
      if (this._riseAccumMs >= this.minOpenMs) {
        this._state = 'speech';
        this._openElapsedMs = 0;
        this._riseAccumMs = this.minOpenMs;
        return 'speech';
      }
      this._state = 'hold';
      return 'hold';
    }

    this._riseAccumMs = 0;
    if (this._state !== 'silence') {
      this._state = 'silence';
    }
    return 'silence';
  }
}
