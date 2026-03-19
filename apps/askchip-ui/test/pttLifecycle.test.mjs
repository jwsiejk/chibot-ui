import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createPttLifecycleController } from '../.test-dist/audio/pttLifecycle.js';
import {
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
      isInteractionBlocked: () => false,
    });

    const startPromise = controller.pressStart();
    const releasePromise = controller.pressRelease();
    started.resolve();
    await Promise.all([startPromise, releasePromise]);

    assert.deepEqual(calls, ['begin', 'cancel']);
  });

  it('routes pointer cancel or focus-loss cleanup through the same release-safe path', async () => {
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
      isInteractionBlocked: () => false,
    });

    await controller.pressStart();
    await controller.pressCancel();

    assert.deepEqual(calls, ['begin', 'backend-start', 'finish', 'submit']);
  });
});
