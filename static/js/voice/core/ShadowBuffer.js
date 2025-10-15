const clampMs = (value, fallback) => {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(0, value);
};

const clampByteRate = (value, fallback) => {
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return value;
};

const byteLengthOf = (buffer) => {
  if (!buffer) return 0;
  if (typeof buffer.byteLength === 'number') return buffer.byteLength;
  if (typeof buffer.size === 'number') return buffer.size;
  return 0;
};

const toEntry = (buffer, { durationMs, timecode } = {}) => ({
  buffer,
  byteLength: byteLengthOf(buffer),
  durationMs: Number.isFinite(durationMs) ? Math.max(0, durationMs) : null,
  timecode: Number.isFinite(timecode) ? timecode : null,
});

export class ShadowBuffer {
  constructor({ maxMs = 450, byteRate = 16000 } = {}) {
    this.maxMs = clampMs(maxMs, 450);
    this.byteRate = clampByteRate(byteRate, 16000);
    this._entries = [];
    this._durationMs = 0;
  }

  clear() {
    this._entries = [];
    this._durationMs = 0;
  }

  push(buffer, meta = {}) {
    if (!buffer) return null;
    const entry = toEntry(buffer, meta);
    if (entry.durationMs == null) {
      const estimate = (entry.byteLength / this.byteRate) * 1000;
      entry.durationMs = Number.isFinite(estimate) ? Math.max(0, estimate) : 0;
    }
    this._entries.push(entry);
    this._durationMs += entry.durationMs;
    while (this._durationMs > this.maxMs && this._entries.length > 1) {
      const removed = this._entries.splice(1, 1)[0];
      if (removed && Number.isFinite(removed.durationMs)) {
        this._durationMs -= removed.durationMs;
      }
    }
    if (this._durationMs < 0) {
      this._durationMs = 0;
    }
    return entry;
  }

  drain() {
    const drained = this._entries.map((entry) => ({ ...entry }));
    this.clear();
    return drained;
  }

  stats() {
    const totalBytes = this._entries.reduce((sum, entry) => sum + (entry.byteLength || 0), 0);
    return {
      count: this._entries.length,
      durationMs: this._durationMs,
      totalBytes,
    };
  }

  entries() {
    return this._entries.map((entry) => ({ ...entry }));
  }
}
