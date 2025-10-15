import test from 'node:test';
import assert from 'node:assert/strict';
import { NoiseModel, dbToRms } from '../../static/js/voice/core/NoiseModel.js';

test('noise model updates floor and snr', () => {
  const model = new NoiseModel({ alpha: 0.5 });
  const samples = new Float32Array([0, 0.5, -0.5, 0]);
  const baseDb = model.prime(samples);
  assert.ok(Number.isFinite(baseDb));

  model.observeSilence({ energy: dbToRms(baseDb - 6) });
  const updated = model.getFloor();
  assert.ok(updated < baseDb);

  const snr = model.snr(dbToRms(updated + 6));
  assert.ok(Math.abs(snr - 6) < 0.1);
});
