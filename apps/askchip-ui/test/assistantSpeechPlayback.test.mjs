import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { findNextSpeechMessage } from '../.test-dist/audio/assistantSpeechHelpers.js';

describe('assistant speech playback helper', () => {
  it('selects the completed canonical assistant message that has not started speech yet', () => {
    const result = findNextSpeechMessage([
      { id: 'user-1', role: 'user', status: 'completed', text: 'Hi', metadata: {}, session_id: 's', source: 'typed_input', modality: 'text', created_at: '', committed_at: '', completed_at: '' },
      { id: 'assistant-1', role: 'assistant', status: 'completed', text: 'First', metadata: { speech: { last_started_at: '2026-03-20T00:00:00.000Z' } }, session_id: 's', source: 'model_output', modality: 'text', created_at: '', committed_at: '', completed_at: '' },
      { id: 'assistant-2', role: 'assistant', status: 'completed', text: 'Second', metadata: {}, session_id: 's', source: 'model_output', modality: 'text', created_at: '', committed_at: '', completed_at: '' },
    ]);

    assert.equal(result?.id, 'assistant-2');
  });

  it('does not create a second assistant speech row in the selector path', () => {
    const messages = [
      { id: 'assistant-1', role: 'assistant', status: 'completed', text: 'Only one row', metadata: {}, session_id: 's', source: 'model_output', modality: 'text', created_at: '', committed_at: '', completed_at: '' },
    ];

    const result = findNextSpeechMessage(messages);

    assert.equal(messages.length, 1);
    assert.equal(result?.text, 'Only one row');
  });
});
