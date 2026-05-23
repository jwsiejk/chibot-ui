export const SESSION_MODES = [
  'open_qa',
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
