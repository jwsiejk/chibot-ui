import { DEFAULT_SESSION_MODE, type SessionMode } from './modes';

export const SESSION_STATES = ['ready', 'listening', 'transcribing', 'thinking', 'speaking', 'error'] as const;
export type SessionState = (typeof SESSION_STATES)[number];

export type AskChappyMetadata = {
  askchappy: {
    persona_id: 'ddn_chappy_vptm';
    persona_label: 'Chappy';
    session_mode: SessionMode;
    audience: 'partner_seller_or_se';
    topic: string | null;
    desired_output: 'answer_questions_and_offer_guidance';
    context: {
      customer_name: string | null;
      partner_name: string | null;
      industry: string | null;
      use_case: string | null;
      competitor: string | null;
      meeting_goal: string | null;
    };
  };
};

export const DEFAULT_METADATA: AskChappyMetadata = {
  askchappy: {
    persona_id: 'ddn_chappy_vptm',
    persona_label: 'Chappy',
    session_mode: DEFAULT_SESSION_MODE,
    audience: 'partner_seller_or_se',
    topic: null,
    desired_output: 'answer_questions_and_offer_guidance',
    context: {
      customer_name: null,
      partner_name: null,
      industry: null,
      use_case: null,
      competitor: null,
      meeting_goal: null,
    },
  },
};
