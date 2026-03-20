import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { findNextSpeechMessage, waitForPlaybackStart } from '../.test-dist/audio/assistantSpeechHelpers.js';

function message(overrides = {}) {
  return {
    id: 'assistant-1',
    role: 'assistant',
    status: 'completed',
    text: 'Reply',
    metadata: {},
    session_id: 's',
    source: 'model_output',
    modality: 'text',
    created_at: '',
    committed_at: '',
    completed_at: '',
    ...overrides,
  };
}

class FakeAudio {
  constructor() {
    this.listeners = new Map();
    this.paused = true;
  }

  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) ?? [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }

  removeEventListener(type, handler) {
    const handlers = this.listeners.get(type) ?? [];
    this.listeners.set(type, handlers.filter((item) => item !== handler));
  }

  async play() {
    this.paused = false;
    queueMicrotask(() => this.emit('playing'));
  }

  emit(type) {
    for (const handler of this.listeners.get(type) ?? []) {
      handler();
    }
  }
}

describe('assistant speech playback helper', () => {
  it('selects only a newly completed assistant message for auto-play', () => {
    const result = findNextSpeechMessage({
      previousMessages: [message({ id: 'assistant-2', status: 'streaming', text: 'Hello' })],
      messages: [message({ id: 'assistant-2', status: 'completed', text: 'Hello there' })],
      sessionChanged: false,
    });

    assert.equal(result?.id, 'assistant-2');
  });

  it('does not auto-play historical transcript messages on initial load', () => {
    const result = findNextSpeechMessage({
      previousMessages: [],
      messages: [message({ id: 'assistant-2' })],
      sessionChanged: false,
    });

    assert.equal(result, null);
  });

  it('does not auto-play historical transcript messages after session selection', () => {
    const result = findNextSpeechMessage({
      previousMessages: [message({ id: 'assistant-old' })],
      messages: [message({ id: 'assistant-old' })],
      sessionChanged: true,
    });

    assert.equal(result, null);
  });

  it('does not create a second assistant speech row in the selector path', () => {
    const messages = [message({ id: 'assistant-1', text: 'Only one row' })];

    const result = findNextSpeechMessage({
      previousMessages: [message({ id: 'assistant-1', status: 'streaming', text: '' })],
      messages,
      sessionChanged: false,
    });

    assert.equal(messages.length, 1);
    assert.equal(result?.text, 'Only one row');
  });

  it('waits for actual playback before resolving speech start acknowledgement sequencing', async () => {
    const audio = new FakeAudio();

    await waitForPlaybackStart(audio);

    assert.equal(audio.paused, false);
  });
});
