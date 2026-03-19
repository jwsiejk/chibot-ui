import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  CONTRACT_TURN_STATES,
  buildListeningDraft,
  buildTranscribingDraft,
  isTurnState,
} from '../.test-dist/state/controllerHelpers.js';

const canonicalMessage = {
  id: 'msg-1',
  session_id: 'session-1',
  role: 'user',
  source: 'voice_input',
  modality: 'voice',
  status: 'committed',
  text: 'Persisted transcript',
  created_at: '2026-03-19T00:00:00.000Z',
  committed_at: '2026-03-19T00:00:01.000Z',
  completed_at: null,
  metadata: {},
};

describe('voice draft helpers', () => {
  it('recognizes the expanded contract state vocabulary for Phase 5', () => {
    assert.deepEqual(CONTRACT_TURN_STATES, ['ready', 'listening', 'transcribing', 'thinking', 'error']);
    assert.equal(isTurnState('listening'), true);
    assert.equal(isTurnState('transcribing'), true);
    assert.equal(isTurnState('recording'), false);
  });

  it('keeps the live listening draft separate from canonical transcript text', () => {
    const draft = buildListeningDraft(1000, 1600);

    assert.equal(draft.mode, 'listening');
    assert.equal(draft.durationMs, 600);
    assert.equal(canonicalMessage.text, 'Persisted transcript');
  });

  it('builds an honest local-only transcribing draft instead of mutating committed transcript rows', () => {
    const before = [canonicalMessage];
    const draft = buildTranscribingDraft(2500);
    const after = [canonicalMessage];

    assert.equal(draft.mode, 'transcribing');
    assert.match(draft.text, /faster-whisper/i);
    assert.deepEqual(after, before);
  });
});
