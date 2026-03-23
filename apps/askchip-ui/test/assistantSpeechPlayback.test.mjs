import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  AssistantSpeechPlaybackCanceledError,
  cleanupFetchedAssistantSpeech,
  createBackendSpeechStartHandshake,
  createPlaybackAttemptTracker,
  findNextSpeechChunk,
  getNextSpeechChunk,
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
  it('selects a stable sentence chunk from the latest streaming assistant message', () => {
    const result = findNextSpeechChunk({
      previousMessages: [message({ id: 'assistant-2', status: 'streaming', text: 'Hello there' })],
      messages: [message({ id: 'assistant-2', status: 'streaming', text: 'Hello there. Nice to see you' })],
      spokenOffsets: new Map(),
      sessionChanged: false,
    });

    assert.equal(result?.message.id, 'assistant-2');
    assert.equal(result?.chunkText, 'Hello there.');
  });

  it('does not auto-play historical transcript messages on initial load', () => {
    const result = findNextSpeechChunk({
      previousMessages: [],
      messages: [message({ id: 'assistant-2' })],
      spokenOffsets: new Map(),
      sessionChanged: false,
    });

    assert.equal(result, null);
  });

  it('does not auto-play historical transcript messages after session selection', () => {
    const result = findNextSpeechChunk({
      previousMessages: [message({ id: 'assistant-old' })],
      messages: [message({ id: 'assistant-old' })],
      spokenOffsets: new Map(),
      sessionChanged: true,
    });

    assert.equal(result, null);
  });

  it('does not create a second assistant speech row in the selector path', () => {
    const messages = [message({ id: 'assistant-1', text: 'Only one row' })];

    const result = findNextSpeechChunk({
      previousMessages: [message({ id: 'assistant-1', status: 'streaming', text: '' })],
      messages,
      spokenOffsets: new Map(),
      sessionChanged: false,
    });

    assert.equal(messages.length, 1);
    assert.equal(result, null);
  });

  it('does not repeat already spoken text when a later stable sentence arrives', () => {
    const result = findNextSpeechChunk({
      previousMessages: [message({ id: 'assistant-3', status: 'streaming', text: 'First sentence.' })],
      messages: [message({ id: 'assistant-3', status: 'streaming', text: 'First sentence. Second sentence.' })],
      spokenOffsets: new Map([['assistant-3', 'First sentence.'.length]]),
      sessionChanged: false,
    });

    assert.equal(result?.chunkText, 'Second sentence.');
  });

  it('speaks the final tail after completion even without trailing sentence punctuation', () => {
    const result = getNextSpeechChunk(message({ id: 'assistant-tail', status: 'completed', text: 'Short final wrap up' }), 0);

    assert.equal(result?.chunkText, 'Short final wrap up');
    assert.equal(result?.spokenThrough, 'Short final wrap up'.length);
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

  it('typed submit invalidates a same-session pending speech reservation before activation', () => {
    const tracker = createPlaybackAttemptTracker();
    const pending = tracker.reserve('session-a', 'assistant-1');

    const invalidated = tracker.invalidate();

    assert.equal(invalidated?.token, pending.token);
    assert.equal(tracker.isCurrent(pending), false);
    assert.equal(tracker.current(), null);
  });

  it('PTT press invalidates a same-session pending speech reservation before activation and allows a clean retry', () => {
    const tracker = createPlaybackAttemptTracker();
    const pending = tracker.reserve('session-a', 'assistant-1');

    tracker.invalidate(pending);
    const retried = tracker.reserve('session-a', 'assistant-1');

    assert.equal(tracker.isCurrent(pending), false);
    assert.equal(tracker.isCurrent(retried), true);
    assert.notEqual(retried.token, pending.token);
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

  it('an explicitly invalidated in-flight fetch result is discarded and cleaned up immediately', () => {
    let revoked = null;
    const originalRevoke = URL.revokeObjectURL;
    URL.revokeObjectURL = (value) => {
      revoked = value;
    };

    const tracker = createPlaybackAttemptTracker();
    const staleAttempt = tracker.reserve('session-a', 'assistant-1');
    tracker.invalidate(staleAttempt);

    const audio = {
      currentTime: 7,
      pauseCalls: 0,
      pause() {
        this.pauseCalls += 1;
      },
    };

    if (!tracker.isCurrent(staleAttempt)) {
      cleanupFetchedAssistantSpeech({ audio, objectUrl: 'blob:invalidated-audio' });
    }

    URL.revokeObjectURL = originalRevoke;
    assert.equal(audio.pauseCalls, 1);
    assert.equal(audio.currentTime, 0);
    assert.equal(revoked, 'blob:invalidated-audio');
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

  it('suppresses a late backend start acknowledgement after typed submit invalidates the pending attempt', async () => {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      stopCalls.push(reason);
    });
    const tracker = createPlaybackAttemptTracker();
    const pending = tracker.reserve('session-a', 'assistant-1');

    handshake.beginStart();
    tracker.invalidate(pending);
    if (!tracker.isCurrent(pending)) {
      await handshake.cancel('typed_submit');
    }
    await handshake.acknowledgeStart();

    assert.deepEqual(stopCalls, ['typed_submit']);
    assert.equal(handshake.state, 'stopped');
  });

  it('suppresses a late backend start acknowledgement after PTT press invalidates the pending attempt', async () => {
    const stopCalls = [];
    const handshake = createBackendSpeechStartHandshake(async (reason) => {
      stopCalls.push(reason);
    });
    const tracker = createPlaybackAttemptTracker();
    const pending = tracker.reserve('session-a', 'assistant-1');

    handshake.beginStart();
    tracker.invalidate(pending);
    if (!tracker.isCurrent(pending)) {
      await handshake.cancel('ptt_start');
    }
    await handshake.acknowledgeStart();

    assert.deepEqual(stopCalls, ['ptt_start']);
    assert.equal(handshake.state, 'stopped');
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
