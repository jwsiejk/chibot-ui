import test from 'node:test';
import assert from 'node:assert/strict';
import {
  computeEnergy,
  toArrayBuffer,
  computePreRollDuration,
  bufferPreRollFrame,
  flushShadowBuffer,
} from '../../static/js/voice/core/FrameUtils.js';
import { ShadowBuffer } from '../../static/js/voice/core/ShadowBuffer.js';

test('computeEnergy returns RMS magnitude', () => {
  const samples = new Float32Array([0, 0.5, -0.5, 1]);
  const energy = computeEnergy(samples);
  const expected = Math.sqrt((0 ** 2 + 0.5 ** 2 + (-0.5) ** 2 + 1 ** 2) / samples.length);
  assert.ok(Math.abs(energy - expected) < 1e-6);
});

test('toArrayBuffer resolves for typed arrays', async () => {
  const view = new Uint8Array([1, 2, 3, 4]);
  const buf = await toArrayBuffer(view.subarray(1, 3));
  assert.equal(buf.byteLength, 2);
  const copy = new Uint8Array(buf);
  assert.deepEqual([...copy], [2, 3]);
});

test('computePreRollDuration tracks last timecode', () => {
  const result1 = computePreRollDuration({ timecode: 120, timeslice: 50, fallbackMs: 60 });
  assert.equal(result1.durationMs, 120);
  const result2 = computePreRollDuration({ timecode: 200, lastTimecode: result1.nextTimecode, timeslice: 50, fallbackMs: 60 });
  assert.equal(result2.durationMs, 80);
});

test('bufferPreRollFrame and flushShadowBuffer integrate with ShadowBuffer', () => {
  const shadow = new ShadowBuffer({ maxMs: 500 });
  let bufferedDuration = 0;
  let bufferedBytes = 0;
  const pushResult = bufferPreRollFrame({
    shadowBuffer: shadow,
    blob: { size: 3 },
    timecode: 150,
    timeslice: 0,
    fallbackMs: 50,
    onBuffered: ({ durationMs, byteLength }) => {
      bufferedDuration = durationMs;
      bufferedBytes = byteLength;
    },
  });
  assert.equal(pushResult.pushed, true);
  assert.equal(pushResult.durationMs, 150);
  assert.equal(bufferedDuration, 150);
  assert.equal(bufferedBytes, 3);
  const stats = flushShadowBuffer(shadow, (buffer, { durationMs, timecode }) => {
    assert.equal(buffer.size, 3);
    assert.equal(durationMs, 150);
    assert.equal(timecode, 150);
  });
  assert.equal(stats.count, 1);
  assert.equal(stats.durationMs, 150);
  assert.ok(stats.totalBytes > 0);
  const empty = flushShadowBuffer(shadow);
  assert.deepEqual(empty, { count: 0, durationMs: 0, totalBytes: 0 });
});
