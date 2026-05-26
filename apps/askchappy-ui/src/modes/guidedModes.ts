import { type SessionMode } from '../../../../shared/contracts/modes';

export type ModeDefinition = {
  mode: SessionMode;
  title: string;
  guidance: string;
};

export const MODE_DEFINITIONS: ModeDefinition[] = [
  {
    mode: 'open_qa',
    title: 'Open Q&A',
    guidance: 'Ask Chappy anything about DDN positioning, use cases, or partner scenarios.',
  },
  {
    mode: 'create_presentations',
    title: 'Create Presentations',
    guidance: 'Guided deck-building mode for creating a professional presentation brief.',
  },
  {
    mode: 'learn_ddn',
    title: 'Learn DDN',
    guidance: 'Build foundational DDN understanding from basics to field usage.',
  },
  {
    mode: 'meeting_prep',
    title: 'Meeting Prep',
    guidance: 'Prepare meeting objectives, agenda, discovery questions, and talk tracks.',
  },
  {
    mode: 'pitch_practice',
    title: 'Pitch Practice',
    guidance: 'Practice your pitch and improve clarity/value alignment.',
  },
  {
    mode: 'objection_handling',
    title: 'Objection Handling',
    guidance: 'Build concise responses to likely pushback.',
  },
  {
    mode: 'competitive_positioning',
    title: 'Competitive Positioning',
    guidance: 'Compare by use case and outcomes while staying within safe claim boundaries.',
  },
  {
    mode: 'technical_deep_dive',
    title: 'Technical Deep Dive',
    guidance: 'Go deeper on architecture, integration, and operational considerations.',
  },
  {
    mode: 'follow_up_builder',
    title: 'Follow-up Builder',
    guidance: 'Prepare follow-up content and next-step messaging.',
  },
];

export const MODE_LOOKUP = Object.fromEntries(MODE_DEFINITIONS.map((item) => [item.mode, item])) as Record<
  SessionMode,
  ModeDefinition
>;

export const GUIDED_MODE_CARDS = MODE_DEFINITIONS.filter((mode) => mode.mode !== 'open_qa');
