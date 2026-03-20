export interface VoiceTurnPayload {
  blob: Blob;
  durationMs: number;
  mimeType: string;
}

export interface PttLifecycleDependencies {
  beginLocalCapture: () => Promise<void>;
  finishLocalCapture: () => Promise<VoiceTurnPayload>;
  cancelLocalCapture: () => void;
  submitVoiceTurn: (recorded: VoiceTurnPayload) => Promise<void>;
  startBackendVoiceTurn: () => Promise<void>;
  cancelBackendVoiceTurn: () => Promise<void>;
  isInteractionBlocked: () => boolean;
}

export interface PttLifecycleController {
  pressStart: () => Promise<void>;
  pressRelease: () => Promise<void>;
  pressCancel: () => Promise<void>;
  dispose: () => void;
}

export function createPttLifecycleController(deps: PttLifecycleDependencies): PttLifecycleController {
  let pressed = false;
  let releaseRequested = false;
  let cancelRequested = false;
  let backendStarted = false;
  let captureReady = false;
  let releasedBeforeCaptureReady = false;
  let startFlow: Promise<void> | null = null;
  let completionFlow: Promise<void> | null = null;
  let cancelFlow: Promise<void> | null = null;

  function resetFlags() {
    pressed = false;
    releaseRequested = false;
    cancelRequested = false;
    backendStarted = false;
    captureReady = false;
    releasedBeforeCaptureReady = false;
    startFlow = null;
    completionFlow = null;
    cancelFlow = null;
  }

  async function completeVoiceTurn() {
    if (completionFlow) {
      await completionFlow;
      return;
    }

    completionFlow = (async () => {
      const recorded = await deps.finishLocalCapture();
      try {
        await deps.submitVoiceTurn(recorded);
      } finally {
        resetFlags();
      }
    })();

    await completionFlow;
  }

  async function maybeFinalizeAfterStart() {
    if (!backendStarted) {
      deps.cancelLocalCapture();
      resetFlags();
      return;
    }

    if (cancelRequested) {
      await cancelVoiceTurn();
      return;
    }

    if (releaseRequested) {
      await completeVoiceTurn();
    }
  }

  async function cancelVoiceTurn() {
    if (cancelFlow) {
      await cancelFlow;
      return;
    }

    cancelFlow = (async () => {
      deps.cancelLocalCapture();
      try {
        if (backendStarted) {
          await deps.cancelBackendVoiceTurn();
        }
      } finally {
        resetFlags();
      }
    })();

    await cancelFlow;
  }

  return {
    async pressStart() {
      if (pressed || startFlow || completionFlow || deps.isInteractionBlocked()) {
        return;
      }

      pressed = true;
      releaseRequested = false;
      cancelRequested = false;
      backendStarted = false;

      startFlow = (async () => {
        try {
          await deps.beginLocalCapture();
          captureReady = true;
          if (releasedBeforeCaptureReady) {
            deps.cancelLocalCapture();
            resetFlags();
            return;
          }
          if (!pressed && !releaseRequested && !cancelRequested) {
            deps.cancelLocalCapture();
            resetFlags();
            return;
          }
          await deps.startBackendVoiceTurn();
          backendStarted = true;
          await maybeFinalizeAfterStart();
        } catch (error) {
          deps.cancelLocalCapture();
          resetFlags();
          throw error;
        } finally {
          startFlow = null;
        }
      })();

      await startFlow;
    },

    async pressRelease() {
      if (!pressed && !startFlow && !backendStarted) {
        return;
      }

      pressed = false;
      releaseRequested = true;

      if (startFlow) {
        if (!captureReady) {
          releasedBeforeCaptureReady = true;
        }
        await startFlow;
        return;
      }

      if (backendStarted) {
        await completeVoiceTurn();
        return;
      }

      deps.cancelLocalCapture();
      resetFlags();
    },

    async pressCancel() {
      if (!pressed && !startFlow && !backendStarted) {
        return;
      }

      pressed = false;
      cancelRequested = true;

      if (startFlow) {
        await startFlow;
        return;
      }

      if (backendStarted) {
        await cancelVoiceTurn();
        return;
      }

      deps.cancelLocalCapture();
      resetFlags();
    },

    dispose() {
      pressed = false;
      releaseRequested = false;
      cancelRequested = true;
      void cancelVoiceTurn();
    },
  };
}
