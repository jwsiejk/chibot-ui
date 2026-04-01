import type { TurnState } from '../types/contract';

export const TURN_STATE_COPY: Record<TurnState, { label: string; detail: string }> = {
  ready: {
    label: 'Ready',
    detail: 'Chip is ready for your next message.',
  },
  listening: {
    label: 'Listening',
    detail: 'Mic is live. Release to send your voice turn.',
  },
  transcribing: {
    label: 'Transcribing',
    detail: 'Converting your audio into text.',
  },
  thinking: {
    label: 'Thinking',
    detail: 'Chip is preparing a response.',
  },
  speaking: {
    label: 'Speaking',
    detail: 'Chip is speaking now.',
  },
  error: {
    label: 'Error',
    detail: 'Something went wrong. Try again.',
  },
};
