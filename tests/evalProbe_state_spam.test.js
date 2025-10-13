const assert = require('assert');

const { evalProbe } = require('../e2e_orchestrator');

const baseTs = 1_700_000_000; // seconds since epoch
const adminLogs = [
  { ts: baseTs, label: 'state: ready', phase: 'ready' },
  { ts: baseTs + 0.18, label: 'state: ready', phase: 'ready' },
  { ts: baseTs + 0.42, label: 'state: ready', phase: 'ready' },
  { ts: baseTs + 2, label: 'state: ready', phase: 'ready' }
];

const probe = {
  expect_signals: [
    {
      stream: 'admin',
      must_not_spam_state: {
        window_ms: 500
      }
    }
  ]
};

const result = evalProbe(probe, adminLogs, [], []);

assert.strictEqual(result.passed, false, 'Expected probe to fail when state spam is detected');
assert(
  result.findings.some(f => /state[:\s]+ready\s+emitted\s+3×\s+within\s+500\s+ms/i.test(f)),
  `Expected findings to mention state spam, got: ${result.findings.join('; ')}`
);

console.log('State spam detection check passed.');
