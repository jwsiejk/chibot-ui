const clampDuration = (value, fallback) => {
  if (!Number.isFinite(value) || value <= 0) {
    return fallback;
  }
  return value;
};

const coerceNumber = (value, fallback = 0) => {
  if (!Number.isFinite(value)) return fallback;
  return value;
};

export function computeEnergy(float32) {
  if (!float32 || typeof float32.length !== 'number') {
    return 0;
  }
  const { length } = float32;
  if (!Number.isFinite(length) || length <= 0) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < length; i += 1) {
    const sample = typeof float32[i] === 'number' ? float32[i] : 0;
    sum += sample * sample;
  }
  if (sum <= 0) {
    return 0;
  }
  return Math.sqrt(sum / length);
}

export function toArrayBuffer(blobOrChunk) {
  if (blobOrChunk == null) {
    return Promise.resolve(null);
  }
  if (blobOrChunk instanceof ArrayBuffer) {
    return Promise.resolve(blobOrChunk);
  }
  if (ArrayBuffer.isView(blobOrChunk)) {
    const view = blobOrChunk;
    const start = Number.isFinite(view.byteOffset) ? view.byteOffset : 0;
    const end = start + (Number.isFinite(view.byteLength) ? view.byteLength : view.buffer.byteLength - start);
    try {
      return Promise.resolve(view.buffer.slice(start, end));
    } catch (err) {
      return Promise.reject(err);
    }
  }
  if (typeof blobOrChunk?.arrayBuffer === 'function') {
    try {
      return Promise.resolve(blobOrChunk.arrayBuffer());
    } catch (err) {
      return Promise.reject(err);
    }
  }
  if (blobOrChunk?.buffer instanceof ArrayBuffer) {
    const buffer = blobOrChunk.buffer;
    const byteOffset = Number.isFinite(blobOrChunk.byteOffset) ? blobOrChunk.byteOffset : 0;
    const byteLength = Number.isFinite(blobOrChunk.byteLength)
      ? blobOrChunk.byteLength
      : buffer.byteLength - byteOffset;
    try {
      return Promise.resolve(buffer.slice(byteOffset, byteOffset + byteLength));
    } catch (err) {
      return Promise.reject(err);
    }
  }
  if (typeof blobOrChunk === 'string') {
    const encoded = new TextEncoder().encode(blobOrChunk);
    return Promise.resolve(encoded.buffer);
  }
  if (typeof blobOrChunk === 'object' && blobOrChunk !== null) {
    try {
      const json = JSON.stringify(blobOrChunk);
      const encoded = new TextEncoder().encode(json);
      return Promise.resolve(encoded.buffer);
    } catch (err) {
      return Promise.reject(err);
    }
  }
  return Promise.resolve(null);
}

export function computePreRollDuration({
  timecode = null,
  lastTimecode = null,
  timeslice = 0,
  fallbackMs = 0,
} = {}) {
  const base = clampDuration(coerceNumber(timeslice, 0), coerceNumber(fallbackMs, 0));
  let durationMs = base;
  let nextTimecode = lastTimecode;

  if (Number.isFinite(timecode)) {
    if (Number.isFinite(lastTimecode)) {
      durationMs = Math.max(0, timecode - lastTimecode);
    } else if (timecode > 0) {
      durationMs = timecode;
    }
    nextTimecode = timecode;
  }

  if (!Number.isFinite(durationMs) || durationMs <= 0) {
    durationMs = base;
  }

  durationMs = Number.isFinite(durationMs) ? Math.max(0, durationMs) : 0;

  return { durationMs, nextTimecode };
}

export function bufferShadowChunk(shadowBuffer, entry = {}) {
  if (!shadowBuffer || !entry || !entry.blob) {
    return { pushed: false, durationMs: 0, byteLength: 0, timecode: null };
  }

  const durationMs = Number.isFinite(entry.durationMs) ? Math.max(0, entry.durationMs) : 0;
  const timecode = Number.isFinite(entry.timecode) ? entry.timecode : null;
  const pushed = shadowBuffer.push(entry.blob, { durationMs, timecode });
  const byteLength = Number.isFinite(pushed?.byteLength)
    ? pushed.byteLength
    : (typeof entry.blob.size === 'number' ? entry.blob.size : 0);

  return {
    pushed: true,
    durationMs,
    byteLength,
    timecode,
  };
}

export function bufferPreRollFrame({
  shadowBuffer,
  blob,
  timecode = null,
  timeslice = 0,
  fallbackMs = 0,
  lastTimecode = null,
  onBuffered = null,
} = {}) {
  const { durationMs, nextTimecode } = computePreRollDuration({
    timecode,
    lastTimecode,
    timeslice,
    fallbackMs,
  });
  const result = bufferShadowChunk(shadowBuffer, { blob, durationMs, timecode });
  if (result.pushed && typeof onBuffered === 'function') {
    onBuffered({
      durationMs: result.durationMs,
      byteLength: result.byteLength,
      timecode: Number.isFinite(timecode) ? timecode : null,
    });
  }
  return {
    ...result,
    durationMs: result.durationMs,
    nextTimecode,
  };
}

export function drainShadowBuffer(shadowBuffer, onChunk = null) {
  if (!shadowBuffer) {
    return { count: 0, durationMs: 0, totalBytes: 0 };
  }

  const drained = shadowBuffer.drain();
  let count = 0;
  let durationMs = 0;
  let totalBytes = 0;

  for (const entry of drained) {
    const buffer = entry?.buffer;
    if (!buffer) continue;

    const chunkDuration = Number.isFinite(entry.durationMs) ? entry.durationMs : 0;
    const byteLength = Number.isFinite(entry.byteLength)
      ? entry.byteLength
      : (typeof buffer.size === 'number' ? buffer.size : 0);

    count += 1;
    durationMs += chunkDuration;
    totalBytes += byteLength;

    if (typeof onChunk === 'function') {
      onChunk({
        buffer,
        durationMs: chunkDuration,
        timecode: Number.isFinite(entry.timecode) ? entry.timecode : null,
        byteLength,
      });
    }
  }

  return { count, durationMs, totalBytes };
}

export function flushShadowBuffer(shadowBuffer, sendChunk, afterFlush = null) {
  const stats = drainShadowBuffer(shadowBuffer, ({ buffer, durationMs, timecode }) => {
    if (typeof sendChunk === 'function') {
      sendChunk(buffer, { durationMs, timecode });
    }
  });
  if (typeof afterFlush === 'function') {
    afterFlush(stats);
  }
  return stats;
}

export function resetShadowBufferState(target) {
  if (!target) {
    return;
  }
  target.preRollLastTimecode = null;
  if (target.shadowBuffer) {
    target.shadowBuffer.clear();
  }
}
