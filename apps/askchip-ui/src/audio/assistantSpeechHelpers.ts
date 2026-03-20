import type { TranscriptMessage } from '../types/contract';

export class AssistantSpeechPlaybackCanceledError extends Error {
  constructor(message = 'Assistant speech playback was canceled before start.') {
    super(message);
    this.name = 'AssistantSpeechPlaybackCanceledError';
  }
}

export type BackendSpeechStartState = 'not_started' | 'starting' | 'started' | 'canceled_before_ack' | 'stopped';

export interface BackendSpeechStartHandshake {
  readonly state: BackendSpeechStartState;
  beginStart(): void;
  cancel(reason: string): Promise<void>;
  acknowledgeStart(): Promise<void>;
  failStart(): void;
}

export function hasSpeechStarted(message: TranscriptMessage): boolean {
  const speech = message.metadata?.speech;
  return typeof speech === 'object' && speech !== null && 'last_started_at' in speech;
}

export function findNextSpeechMessage(params: {
  messages: TranscriptMessage[];
  previousMessages: TranscriptMessage[];
  sessionChanged: boolean;
}): TranscriptMessage | null {
  if (params.sessionChanged || params.previousMessages.length === 0) {
    return null;
  }

  const previousById = new Map(params.previousMessages.map((message) => [message.id, message]));
  const eligible = [...params.messages]
    .filter((message) => message.role === 'assistant' && message.status === 'completed' && message.text.trim())
    .filter((message) => {
      const previous = previousById.get(message.id);
      if (!previous) {
        return false;
      }
      if (hasSpeechStarted(message)) {
        return false;
      }
      return previous.status !== 'completed' || !previous.text.trim();
    });

  return eligible.length > 0 ? eligible[eligible.length - 1] : null;
}

function normalizePlaybackError(error: unknown): Error {
  if (error instanceof Error) {
    return error;
  }
  return new Error('Assistant speech playback failed.');
}

export function waitForPlaybackStart(
  audio: Pick<HTMLAudioElement, 'addEventListener' | 'removeEventListener' | 'play' | 'paused'>,
  options: { signal?: AbortSignal; cancellationError?: Error } = {},
): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cancellationError = options.cancellationError ?? new AssistantSpeechPlaybackCanceledError();

    const cleanup = () => {
      audio.removeEventListener('playing', handlePlaying);
      options.signal?.removeEventListener('abort', handleAbort);
    };

    const finish = (callback: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      callback();
    };

    const handlePlaying = () => finish(resolve);
    const handleAbort = () => finish(() => reject(cancellationError));

    if (options.signal?.aborted) {
      handleAbort();
      return;
    }

    audio.addEventListener('playing', handlePlaying, { once: true });
    options.signal?.addEventListener('abort', handleAbort, { once: true });

    Promise.resolve(audio.play())
      .then(() => {
        if (options.signal?.aborted) {
          handleAbort();
          return;
        }
        if (!audio.paused) {
          finish(resolve);
        }
      })
      .catch((error) => finish(() => reject(normalizePlaybackError(error))));
  });
}

export function createBackendSpeechStartHandshake(sendStop: (reason: string) => Promise<void>): BackendSpeechStartHandshake {
  let state: BackendSpeechStartState = 'not_started';
  let canceledReason: string | null = null;
  let stopPromise: Promise<void> | null = null;

  const stopExactlyOnce = (reason: string) => {
    if (stopPromise) {
      return stopPromise;
    }
    state = 'stopped';
    stopPromise = sendStop(reason);
    return stopPromise;
  };

  return {
    get state() {
      return state;
    },
    beginStart() {
      if (state === 'not_started') {
        state = 'starting';
      }
    },
    async cancel(reason: string) {
      canceledReason = reason;
      if (state === 'started') {
        await stopExactlyOnce(reason);
        return;
      }
      if (state === 'starting') {
        state = 'canceled_before_ack';
      }
    },
    async acknowledgeStart() {
      if (state === 'starting') {
        state = 'started';
        return;
      }
      if (state === 'canceled_before_ack') {
        await stopExactlyOnce(canceledReason ?? 'stopped');
      }
    },
    failStart() {
      if (state === 'starting' || state === 'canceled_before_ack') {
        state = 'not_started';
      }
    },
  };
}
