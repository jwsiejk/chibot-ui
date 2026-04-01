import type { TurnState } from '../types/contract';

export const TURN_STATE_COPY: Record<TurnState, { label: string; detail: string }> = {
  ready: {
    label: 'Ready',
    detail: 'The backend is idle and available for typed or push-to-talk input.',
  },
  listening: {
    label: 'Listening',
    detail: 'Push-to-talk capture is active and waiting for release before any voice turn can commit.',
  },
  transcribing: {
    label: 'Transcribing',
    detail: 'The released push-to-talk audio is being transcribed into one final user transcript before commit.',
  },
  thinking: {
    label: 'Thinking',
    detail: 'A canonical user turn is committed and the assistant is generating a text response.',
  },
  speaking: {
    label: 'Speaking',
    detail: 'A stable chunk from the canonical assistant message is actively playing through the local Kokoro speech path, even if generation is still finishing.',
  },
  error: {
    label: 'Error',
    detail: 'The backend reported an assistant or voice-input failure that needs attention.',
  },
};
