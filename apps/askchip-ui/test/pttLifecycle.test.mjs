import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createPttLifecycleController } from '../.test-dist/audio/pttLifecycle.js';
import {
  getRecoveredVoiceTopLevelState,
  getSendingDisabledReason,
  getVoiceDisabledReason,
} from '../.test-dist/state/controllerHelpers.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('frontend gating helpers', () => {
  it('disables typed send while voice is listening or transcribing', () => {
    assert.match(
      getSendingDisabledReason({ currentSessionId: 'session-1', pendingTurn: false, topLevelState: 'listening' }) ?? '',
      /push-to-talk/i,
    );
    assert.match(
      getSendingDisabledReason({ currentSessionId: 'session-1', pendingTurn: false, topLevelState: 'transcribing' }) ?? '',
      /push-to-talk/i,
    );
    assert.equal(
      getVoiceDisabledReason({ currentSessionId: 'session-1', pendingTurn: false, topLevelState: 'thinking' }),
      'Wait for the current assistant turn to finish before recording another voice turn.',
    );
  });

  it('recovers local voice state to ready unless the backend is already thinking', () => {
    assert.equal(getRecoveredVoiceTopLevelState('listening'), 'ready');
    assert.equal(getRecoveredVoiceTopLevelState('transcribing'), 'ready');
    assert.equal(getRecoveredVoiceTopLevelState('thinking'), 'thinking');
  });
});

describe('PTT lifecycle controller', () => {
  it('cleans up a quick press/release before async capture setup finishes', async () => {
    const started = deferred();
    const calls = [];
    const controller = createPttLifecycleController({
      beginLocalCapture: async () => {
        calls.push('begin');
        await started.promise;
      },
      finishLocalCapture: async () => {
        calls.push('finish');
        return { blob: new Blob(['voice']), durationMs: 12, mimeType: 'audio/webm' };
      },
      cancelLocalCapture: () => {
        calls.push('cancel');
      },
      submitVoiceTurn: async () => {
        calls.push('submit');
      },
      startBackendVoiceTurn: async () => {
        calls.push('backend-start');
      },
      cancelBackendVoiceTurn: async () => {
        calls.push('backend-cancel');
      },
      isInteractionBlocked: () => false,
    });

    const startPromise = controller.pressStart();
    const releasePromise = controller.pressRelease();
    started.resolve();
    await Promise.all([startPromise, releasePromise]);

    assert.deepEqual(calls, ['begin', 'cancel']);
  });

  it('routes pointer cancel and focus-loss cleanup through discard semantics instead of submit', async () => {
    const calls = [];
    const controller = createPttLifecycleController({
      beginLocalCapture: async () => {
        calls.push('begin');
      },
      finishLocalCapture: async () => {
        calls.push('finish');
        return { blob: new Blob(['voice']), durationMs: 25, mimeType: 'audio/webm' };
      },
      cancelLocalCapture: () => {
        calls.push('cancel');
      },
      submitVoiceTurn: async () => {
        calls.push('submit');
      },
      startBackendVoiceTurn: async () => {
        calls.push('backend-start');
      },
      cancelBackendVoiceTurn: async () => {
        calls.push('backend-cancel');
      },
      isInteractionBlocked: () => false,
    });

    await controller.pressStart();
    await controller.pressCancel();

    assert.deepEqual(calls, ['begin', 'backend-start', 'cancel', 'backend-cancel']);
  });

  it('disposes active capture without submitting a stale release later', async () => {
    const calls = [];
    const controller = createPttLifecycleController({
      beginLocalCapture: async () => {
        calls.push('begin');
      },
      finishLocalCapture: async () => {
        calls.push('finish');
        return { blob: new Blob(['voice']), durationMs: 25, mimeType: 'audio/webm' };
      },
      cancelLocalCapture: () => {
        calls.push('cancel');
      },
      submitVoiceTurn: async () => {
        calls.push('submit');
      },
      startBackendVoiceTurn: async () => {
        calls.push('backend-start');
      },
      cancelBackendVoiceTurn: async () => {
        calls.push('backend-cancel');
      },
      isInteractionBlocked: () => false,
    });

    await controller.pressStart();
    controller.dispose();
    await Promise.resolve();
    await controller.pressRelease();

    assert.deepEqual(calls, ['begin', 'backend-start', 'cancel', 'backend-cancel']);
  });

  it('resets completion flow after local capture finalization fails so a new press can start cleanly', async () => {
    const calls = [];
    let finishAttempts = 0;
    const controller = createPttLifecycleController({
      beginLocalCapture: async () => {
        calls.push('begin');
      },
      finishLocalCapture: async () => {
        finishAttempts += 1;
        calls.push(`finish-${finishAttempts}`);
        if (finishAttempts === 1) {
          throw new Error('capture finalization failed');
        }
        return { blob: new Blob(['voice']), durationMs: 25, mimeType: 'audio/webm' };
      },
      cancelLocalCapture: () => {
        calls.push('cancel');
      },
      submitVoiceTurn: async () => {
        calls.push('submit');
      },
      startBackendVoiceTurn: async () => {
        calls.push('backend-start');
      },
      cancelBackendVoiceTurn: async () => {
        calls.push('backend-cancel');
      },
      isInteractionBlocked: () => false,
    });

    await controller.pressStart();
    await assert.rejects(controller.pressRelease(), /capture finalization failed/);

    await controller.pressStart();
    await controller.pressRelease();

    assert.deepEqual(calls, [
      'begin',
      'backend-start',
      'finish-1',
      'begin',
      'backend-start',
      'finish-2',
      'submit',
    ]);
  });
});
