import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { applyAssistantStreamEvent, getSendingDisabledReason, getVoiceDisabledReason } from '../.test-dist/state/controllerHelpers.js';

function buildEvent(overrides = {}) {
  return {
    id: 'event-1',
    session_id: 'session-1',
    turn_id: 'turn-1',
    type: 'assistant.delta',
    payload: { message_id: 'assistant-1', delta: 'Hello' },
    created_at: '2026-03-19T00:00:00.000Z',
    ...overrides,
  };
}

function buildMessage(overrides = {}) {
  return {
    id: 'assistant-1',
    session_id: 'session-1',
    role: 'assistant',
    source: 'model_output',
    modality: 'text',
    status: 'streaming',
    text: 'Hi',
    created_at: '2026-03-19T00:00:00.000Z',
    committed_at: null,
    completed_at: null,
    metadata: { model: 'llama3.2' },
    ...overrides,
  };
}

describe('applyAssistantStreamEvent', () => {
  it('creates a placeholder assistant message when a delta arrives before a local message exists', () => {
    const event = buildEvent({
      payload: { message_id: 'assistant-2', delta: 'Hello', model: 'llama3.2' },
      created_at: '2026-03-19T00:00:01.000Z',
    });

    const nextMessages = applyAssistantStreamEvent([], event);

    assert.deepEqual(nextMessages, [{
      id: 'assistant-2',
      session_id: 'session-1',
      role: 'assistant',
      source: 'model_output',
      modality: 'text',
      status: 'streaming',
      text: 'Hello',
      created_at: '2026-03-19T00:00:01.000Z',
      committed_at: null,
      completed_at: null,
      metadata: { model: 'llama3.2' },
    }]);
  });

  it('appends streaming deltas to the canonical assistant message text', () => {
    const existingMessage = buildMessage({ text: 'Hello' });
    const event = buildEvent({ payload: { message_id: existingMessage.id, delta: ' there' } });

    const nextMessages = applyAssistantStreamEvent([existingMessage], event);

    assert.deepEqual(nextMessages, [{ ...existingMessage, text: 'Hello there' }]);
  });

  it('preserves the backend contract-shaped assistant message object while updating stream fields', () => {
    const existingMessage = buildMessage({
      status: 'committed',
      metadata: { model: 'llama3.2', trace_id: 'trace-7' },
      committed_at: '2026-03-19T00:00:02.000Z',
      completed_at: '2026-03-19T00:00:03.000Z',
    });
    const event = buildEvent({
      type: 'assistant.started',
      payload: { message_id: existingMessage.id, model: 'phi4-mini' },
    });

    const nextMessages = applyAssistantStreamEvent([existingMessage], event);

    assert.deepEqual(nextMessages, [{
      ...existingMessage,
      status: 'streaming',
      metadata: { model: 'phi4-mini', trace_id: 'trace-7' },
    }]);
  });
});


describe('Phase 6 speaking helpers', () => {
  it('does not block typed or voice turn starts while speaking because submit/PTT performs an explicit interrupt first', () => {
    assert.equal(getSendingDisabledReason({ currentSessionId: 'session-1', pendingTurn: false, topLevelState: 'speaking' }), null);
    assert.equal(getVoiceDisabledReason({ currentSessionId: 'session-1', pendingTurn: false, topLevelState: 'speaking' }), null);
  });
});
