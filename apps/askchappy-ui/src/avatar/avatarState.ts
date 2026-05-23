import { SESSION_STATES, type SessionState } from '../../../../shared/contracts/session';

export type AvatarAssetStatus = 'placeholder';

export type ChappyAvatarStateConfig = {
  state: SessionState;
  label: string;
  description: string;
};

export const CHAPPY_AVATAR_STATE_CONFIG: Record<SessionState, ChappyAvatarStateConfig> = {
  ready: {
    state: 'ready',
    label: 'Chappy is ready',
    description: 'Waiting for your next message in this local-first session.',
  },
  listening: {
    state: 'listening',
    label: 'Chappy is listening',
    description: 'Input capture state placeholder for future voice-enabled local production sessions.',
  },
  transcribing: {
    state: 'transcribing',
    label: 'Chappy is transcribing',
    description: 'Speech-to-text placeholder state while transcript contracts remain canonical.',
  },
  thinking: {
    state: 'thinking',
    label: 'Chappy is thinking',
    description: 'Assistant reasoning placeholder for stage feedback only.',
  },
  speaking: {
    state: 'speaking',
    label: 'Chappy is speaking',
    description: 'Speech output placeholder mapped to the canonical assistant transcript.',
  },
  error: {
    state: 'error',
    label: 'Chappy needs attention',
    description: 'Session runtime needs attention while preserving transcript integrity.',
  },
};

export type ChappyAvatarRuntimeStatus = {
  avatar_asset_status: AvatarAssetStatus;
  supports_visemes: false;
  supports_speaking_animation: false;
  current_state: SessionState;
};

export const getChappyAvatarStateConfig = (state: SessionState): ChappyAvatarStateConfig => CHAPPY_AVATAR_STATE_CONFIG[state];

export const getChappyAvatarRuntimeStatus = (currentState: SessionState): ChappyAvatarRuntimeStatus => ({
  avatar_asset_status: 'placeholder',
  supports_visemes: false,
  supports_speaking_animation: false,
  current_state: currentState,
});

export const CHAPPY_AVATAR_STATE_KEYS = SESSION_STATES;
