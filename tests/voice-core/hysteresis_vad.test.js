import test from 'node:test';
import assert from 'node:assert/strict';
import { HysteresisVAD } from '../../static/js/voice/core/HysteresisVAD.js';

test('hysteresis VAD transitions with thresholds', () => {
  const vad = new HysteresisVAD({ enter: 2, exit: 2 });
  assert.equal(vad.push(false), 'silence');
  assert.equal(vad.push(true), 'hold');
  assert.equal(vad.push(true), 'speech');
  assert.equal(vad.push(false), 'hold');
  assert.equal(vad.push(false), 'silence');
});
