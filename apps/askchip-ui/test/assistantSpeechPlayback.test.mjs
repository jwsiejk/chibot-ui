import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  AssistantSpeechPlaybackCanceledError,
  createBackendSpeechStartHandshake,
  findNextSpeechMessage,
  waitForPlaybackStart,
} from '../.test-dist/audio/assistantSpeechHelpers.js';

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

class HangingAudio extends FakeAudio {
  async play() {
    this.paused = true;
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

  it('rejects deterministically when playback is canceled before the playing event fires', async () => {
    const audio = new HangingAudio();
    const controller = new AbortController();
    const wait = waitForPlaybackStart(audio, { signal: controller.signal });

    controller.abort();

    await assert.rejects(wait, AssistantSpeechPlaybackCanceledError);
  });
});

describe('backend speech start handshake', () => {
  async function resolveAfterCleanup(reason) {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (stopReason) => {
      stopCalls.push(stopReason);
    });

    handshake.beginStart();
    const cleanupPromise = handshake.cancel(reason);
    await handshake.acknowledgeStart();
    await cleanupPromise;

    return stopCalls;
  }

  it('sends exactly one backend stop when cleanup lands before the start ack resolves', async () => {
    assert.deepEqual(await resolveAfterCleanup('typed_submit'), ['typed_submit']);
  });

  it('keeps typed-submit interrupts deterministic during the handshake window', async () => {
    assert.deepEqual(await resolveAfterCleanup('typed_submit'), ['typed_submit']);
  });

  it('keeps PTT interrupts deterministic during the handshake window', async () => {
    assert.deepEqual(await resolveAfterCleanup('ptt_start'), ['ptt_start']);
  });

  it('keeps session switch cleanup deterministic during the handshake window', async () => {
    assert.deepEqual(await resolveAfterCleanup('session_switch'), ['session_switch']);
  });

  it('keeps unmount cleanup deterministic during the handshake window', async () => {
    assert.deepEqual(await resolveAfterCleanup('unmount'), ['unmount']);
  });

  it('does not double-stop after cleanup already won the handshake race', async () => {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      stopCalls.push(reason);
    });

    handshake.beginStart();
    await handshake.cancel('session_switch');
    await handshake.acknowledgeStart();
    await handshake.cancel('unmount');

    assert.deepEqual(stopCalls, ['session_switch']);
    assert.equal(handshake.state, 'stopped');
  });

  it('does not send stop when backend start fails before an ack exists', async () => {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      stopCalls.push(reason);
    });

    handshake.beginStart();
    await handshake.cancel('typed_submit');
    handshake.failStart();

    assert.deepEqual(stopCalls, []);
    assert.equal(handshake.state, 'not_started');
  });
});
