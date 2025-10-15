import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  initMic,
  registerVoiceLegacyFacade,
} from '../../static/js/voice/legacy/VoiceLegacyFacade.js';

test('facade delegates after registration', () => {
  assert.throws(() => initMic(), /VoiceLegacyFacade\.initMic not wired/);

  registerVoiceLegacyFacade({ initMic: (value) => value ?? 'ready' });

  assert.equal(initMic('wired'), 'wired');
  assert.equal(initMic(), 'ready');
});
