export const SESSION_MODES = [
  'open_qa',
  'create_presentations',
  'learn_ddn',
  'meeting_prep',
  'pitch_practice',
  'objection_handling',
  'competitive_positioning',
  'technical_deep_dive',
  'follow_up_builder',
] as const;

export type SessionMode = (typeof SESSION_MODES)[number];

export const DEFAULT_SESSION_MODE: SessionMode = 'open_qa';

export const isSessionMode = (value: unknown): value is SessionMode =>
  typeof value === 'string' && SESSION_MODES.includes(value as SessionMode);

export const assertSessionMode = (value: unknown): asserts value is SessionMode => {
  if (!isSessionMode(value)) {
    throw new Error(`Invalid session mode: ${String(value)}`);
  }
};
