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
  failStart(reason: string): Promise<void>;
}

export interface PlaybackAttemptReservation {
  token: number;
  sessionId: string;
  messageId: string;
}

export interface PlaybackAttemptTracker {
  reserve(sessionId: string, messageId: string): PlaybackAttemptReservation;
  isCurrent(attempt: PlaybackAttemptReservation): boolean;
  invalidate(attempt?: PlaybackAttemptReservation): PlaybackAttemptReservation | null;
  clear(attempt: PlaybackAttemptReservation): void;
  hasCurrent(): boolean;
  current(): PlaybackAttemptReservation | null;
}

export interface SpeechChunkCandidate {
  message: TranscriptMessage;
  chunkText: string;
  spokenThrough: number;
}

const MIN_CHUNK_CHARS = 12;
const MIN_CHUNK_WORDS = 2;
const MAX_CHUNK_WINDOW_CHARS = 420;

export function hasSpeechStarted(message: TranscriptMessage): boolean {
  const speech = message.metadata?.speech;
  return typeof speech === 'object' && speech !== null && 'last_started_at' in speech;
}

export function findNextSpeechChunk(params: {
  messages: TranscriptMessage[];
  previousMessages: TranscriptMessage[];
  spokenOffsets: Map<string, number>;
  sessionChanged: boolean;
}): SpeechChunkCandidate | null {
  if (params.sessionChanged || params.previousMessages.length === 0) {
    return null;
  }

  const latestAssistantMessage = [...params.messages].reverse().find(
    (message) => message.role === 'assistant' && message.source === 'model_output' && message.text.trim(),
  );
  if (!latestAssistantMessage) {
    return null;
  }

  const previousById = new Map(params.previousMessages.map((message) => [message.id, message]));
  const previousMessage = previousById.get(latestAssistantMessage.id);
  if (!previousMessage) {
    return null;
  }
  if (
    latestAssistantMessage.status === 'completed'
    && previousMessage.status === 'streaming'
    && !previousMessage.text.trim()
    && !/[.!?]/.test(latestAssistantMessage.text)
  ) {
    return null;
  }

  const spokenOffset = params.spokenOffsets.get(latestAssistantMessage.id) ?? 0;
  return getNextSpeechChunk(latestAssistantMessage, spokenOffset);
}

export function getNextSpeechChunk(message: TranscriptMessage, spokenOffset: number): SpeechChunkCandidate | null {
  if (!message.text.trim()) {
    return null;
  }

  const normalizedSpokenOffset = Math.max(0, Math.min(spokenOffset, message.text.length));
  const remainingText = message.text.slice(normalizedSpokenOffset);
  if (!remainingText.trim()) {
    return null;
  }

  const stableBoundary = findStableBoundaryIndex(remainingText);
  if (stableBoundary !== null) {
    const chunkText = remainingText.slice(0, stableBoundary).trim();
    if (isNaturalSpeechChunk(chunkText)) {
      return {
        message,
        chunkText,
        spokenThrough: normalizedSpokenOffset + stableBoundary,
      };
    }
  }

  if (message.status === 'completed') {
    const finalTail = remainingText.trim();
    if (finalTail && (isNaturalSpeechChunk(finalTail) || normalizedSpokenOffset > 0)) {
      return {
        message,
        chunkText: finalTail,
        spokenThrough: message.text.length,
      };
    }
  }

  return null;
}

function isNaturalSpeechChunk(text: string): boolean {
  return text.length >= MIN_CHUNK_CHARS || text.split(/\s+/).filter(Boolean).length >= MIN_CHUNK_WORDS;
}

function findStableBoundaryIndex(text: string): number | null {
  const maxScanLength = Math.min(text.length, MAX_CHUNK_WINDOW_CHARS);
  let earliestSentenceBoundary: number | null = null;
  let earliestPauseBoundary: number | null = null;

  for (let index = 0; index < maxScanLength; index += 1) {
    const current = text[index];
    const next = text[index + 1] ?? '';
    const trailing = text.slice(index + 1);

    if ((current === '.' || current === '!' || current === '?') && (!next || /\s|["')\]]/.test(next))) {
      const candidate = consumeBoundary(text, index);
      const chunk = text.slice(0, candidate).trim();
      if (isNaturalSpeechChunk(chunk)) {
        earliestSentenceBoundary = candidate;
        break;
      }
      continue;
    }

    if ((current === ';' || current === ':' || current === '\n') && trailing.trim()) {
      const candidate = consumeBoundary(text, index);
      const chunk = text.slice(0, candidate).trim();
      if (earliestPauseBoundary === null && isNaturalSpeechChunk(chunk)) {
        earliestPauseBoundary = candidate;
      }
    }
  }

  if (earliestSentenceBoundary !== null) {
    return earliestSentenceBoundary;
  }
  if (earliestPauseBoundary !== null) {
    return earliestPauseBoundary;
  }
  return null;
}

function consumeBoundary(text: string, index: number): number {
  let cursor = index + 1;
  while (cursor < text.length && /["')\]\s]/.test(text[cursor] ?? '')) {
    cursor += 1;
  }
  return cursor;
}

function normalizePlaybackError(error: unknown): Error {
  if (error instanceof Error) {
    return error;
  }
  return new Error('Assistant speech playback failed.');
}

export function cleanupFetchedAssistantSpeech(playback: { audio: Pick<HTMLAudioElement, 'pause' | 'currentTime'>; objectUrl: string }): void {
  playback.audio.pause();
  playback.audio.currentTime = 0;
  URL.revokeObjectURL(playback.objectUrl);
}

export function createPlaybackAttemptTracker(): PlaybackAttemptTracker {
  let nextToken = 0;
  let currentAttempt: PlaybackAttemptReservation | null = null;

  return {
    reserve(sessionId: string, messageId: string) {
      const attempt = { token: nextToken + 1, sessionId, messageId };
      nextToken += 1;
      currentAttempt = attempt;
      return attempt;
    },
    isCurrent(attempt: PlaybackAttemptReservation) {
      return currentAttempt?.token === attempt.token;
    },
    invalidate(attempt?: PlaybackAttemptReservation) {
      if (attempt && currentAttempt?.token !== attempt.token) {
        return null;
      }
      const invalidatedAttempt = currentAttempt;
      currentAttempt = null;
      return invalidatedAttempt;
    },
    clear(attempt: PlaybackAttemptReservation) {
      if (currentAttempt?.token === attempt.token) {
        currentAttempt = null;
      }
    },
    hasCurrent() {
      return currentAttempt !== null;
    },
    current() {
      return currentAttempt;
    },
  };
}

export async function waitForPlaybackStart(
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
    async failStart(reason: string) {
      if (state === 'starting' || state === 'canceled_before_ack') {
        await stopExactlyOnce(reason);
        return;
      }
      if (state === 'started') {
        await stopExactlyOnce(reason);
      }
    },
  };
}
