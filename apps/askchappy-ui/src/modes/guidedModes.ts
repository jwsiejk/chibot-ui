import type { SessionMode } from '../../../../shared/contracts/modes';

export type GuidedModeCard = {
  mode: Exclude<SessionMode, 'open_qa'>;
  title: string;
};

export const GUIDED_MODE_CARDS: GuidedModeCard[] = [
  { mode: 'learn_ddn', title: 'Learn DDN' },
  { mode: 'meeting_prep', title: 'Meeting Prep' },
  { mode: 'pitch_practice', title: 'Pitch Practice' },
  { mode: 'objection_handling', title: 'Objection Handling' },
  { mode: 'competitive_positioning', title: 'Competitive Positioning' },
  { mode: 'technical_deep_dive', title: 'Technical Deep Dive' },
  { mode: 'follow_up_builder', title: 'Follow-up Builder' },
];
