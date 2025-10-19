import test from 'node:test';
import assert from 'node:assert/strict';
import { HysteresisVAD } from '../../static/js/voice/core/HysteresisVAD.js';

test('hysteresis VAD enforces minimum open window', () => {
  const vad = new HysteresisVAD({ minOpenMs: 180, frameMs: 30 });

  for (let i = 0; i < 5; i += 1) {
    assert.equal(vad.push(-39), 'hold');
  }
  assert.equal(vad.push(-39), 'speech');

  for (let i = 0; i < 5; i += 1) {
    assert.equal(vad.push(-60), 'speech');
  }
  assert.equal(vad.push(-60), 'silence');
});

test('hysteresis VAD resets cleanly on silence', () => {
  const vad = new HysteresisVAD({ openDb: -38, closeDb: -44, minOpenMs: 200, frameMs: 40 });

  assert.equal(vad.push(-90), 'silence');
  assert.equal(vad.push(-90), 'silence');

  for (let i = 0; i < 4; i += 1) {
    assert.equal(vad.push(-35), 'hold');
  }
  assert.equal(vad.push(-35), 'speech');

  vad.reset();
  assert.equal(vad.state, 'silence');
  assert.equal(vad.push(-90), 'silence');
});
