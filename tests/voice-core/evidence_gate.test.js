import test from 'node:test';
import assert from 'node:assert/strict';
import { EvidenceGate } from '../../static/js/voice/core/EvidenceGate.js';

test('evidence gate opens when SNR and partial cues align', () => {
  const gate = new EvidenceGate({ baseSnrDb: 3.5, asrConf: 0.6 });
  gate.start({ startedAt: 0, bufferedMs: 0, bufferedBytes: 0 });
  gate.setDetail({ snrDb: 4 });
  let result = gate.update({
    vadState: 'speech',
    snr: 4,
    snrBoost: 0,
    bufferedMs: 200,
    bufferedBytes: 5000,
    minSpeechMs: 400,
    minBytes: 8000,
  });
  assert.equal(result.shouldCommit, false);
  gate.update({
    vadState: 'hold',
    snr: 4,
    snrBoost: 0,
    bufferedMs: 200,
    bufferedBytes: 5000,
    minSpeechMs: 400,
    minBytes: 8000,
    asrCue: { type: 'partial', conf: 0.7, threshold: 0.6, delta: 0.05 },
  });
  assert.equal(gate.shouldCommit(), true);
});
