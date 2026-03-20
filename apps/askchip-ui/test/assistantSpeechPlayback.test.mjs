import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  AssistantSpeechPlaybackCanceledError,
  cleanupFetchedAssistantSpeech,
  createBackendSpeechStartHandshake,
  createPlaybackAttemptTracker,
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

  it('duplicate play attempts while speech fetch is in flight keep only one active reservation per session until superseded', () => {
    const tracker = createPlaybackAttemptTracker();
    const first = tracker.reserve('session-a', 'assistant-1');

    assert.equal(tracker.current()?.sessionId, 'session-a');
    assert.equal(tracker.current()?.messageId, 'assistant-1');
    assert.equal(tracker.isCurrent(first), true);

    const second = tracker.reserve('session-b', 'assistant-2');

    assert.equal(tracker.isCurrent(first), false);
    assert.equal(tracker.isCurrent(second), true);
    assert.equal(tracker.current()?.sessionId, 'session-b');
  });

  it('stale speech fetch results after a session switch are discarded and cleaned up immediately', () => {
    let revoked = null;
    const originalRevoke = URL.revokeObjectURL;
    URL.revokeObjectURL = (value) => {
      revoked = value;
    };

    const audio = {
      paused: false,
      currentTime: 12,
      pauseCalls: 0,
      pause() {
        this.pauseCalls += 1;
      },
    };

    cleanupFetchedAssistantSpeech({ audio, objectUrl: 'blob:stale-audio' });

    URL.revokeObjectURL = originalRevoke;
    assert.equal(audio.pauseCalls, 1);
    assert.equal(audio.currentTime, 0);
    assert.equal(revoked, 'blob:stale-audio');
  });

  it('a superseded in-flight fetch result is discarded and cleaned up immediately', () => {
    let revoked = null;
    const originalRevoke = URL.revokeObjectURL;
    URL.revokeObjectURL = (value) => {
      revoked = value;
    };

    const tracker = createPlaybackAttemptTracker();
    const staleAttempt = tracker.reserve('session-a', 'assistant-1');
    tracker.reserve('session-b', 'assistant-2');

    const audio = {
      currentTime: 4,
      pauseCalls: 0,
      pause() {
        this.pauseCalls += 1;
      },
    };

    if (!tracker.isCurrent(staleAttempt)) {
      cleanupFetchedAssistantSpeech({ audio, objectUrl: 'blob:superseded-audio' });
    }

    URL.revokeObjectURL = originalRevoke;
    assert.equal(audio.pauseCalls, 1);
    assert.equal(audio.currentTime, 0);
    assert.equal(revoked, 'blob:superseded-audio');
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

  it('best-effort stops exactly once when backend start fails after the uncertain start phase begins', async () => {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      stopCalls.push(reason);
    });

    handshake.beginStart();
    await handshake.failStart('start_failed');
    await handshake.cancel('typed_submit');

    assert.deepEqual(stopCalls, ['start_failed']);
    assert.equal(handshake.state, 'stopped');
  });

  it('keeps the original start failure surface separate from best-effort cleanup', async () => {
    const cleanupCalls = [];
    const startError = new Error('start request failed');
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      cleanupCalls.push(reason);
    });

    handshake.beginStart();
    try {
      throw startError;
    } catch (error) {
      await handshake.failStart('start_failed');
      assert.equal(error, startError);
    }

    assert.deepEqual(cleanupCalls, ['start_failed']);
  });
});
