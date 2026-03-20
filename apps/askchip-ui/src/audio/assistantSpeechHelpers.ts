import type { TranscriptMessage } from '../types/contract';

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

export function waitForPlaybackStart(audio: Pick<HTMLAudioElement, 'addEventListener' | 'removeEventListener' | 'play' | 'paused'>): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      audio.removeEventListener('playing', handlePlaying);
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

    audio.addEventListener('playing', handlePlaying, { once: true });

    Promise.resolve(audio.play())
      .then(() => {
        if (!audio.paused) {
          finish(resolve);
        }
      })
      .catch((error) => finish(() => reject(error instanceof Error ? error : new Error('Assistant speech playback failed.'))));
  });
}
