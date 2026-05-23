export const VOICE_PROFILE_STATES = ['draft', 'testing', 'approved', 'published', 'disabled'] as const;
export type VoiceProfileState = (typeof VOICE_PROFILE_STATES)[number];

export const isVoiceProfileState = (value: unknown): value is VoiceProfileState =>
  typeof value === 'string' && VOICE_PROFILE_STATES.includes(value as VoiceProfileState);
