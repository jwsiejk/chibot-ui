import { DEFAULT_SESSION_MODE, isSessionMode, type SessionMode } from './modes';

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
    create_presentations_state: {
      active: boolean;
      mode: 'create_presentations';
      step: 'intro';
      deckBrief: {
        schema_version: '1.0';
        mode: 'create_presentations';
        status: 'draft';
      };
      messages: string[];
      awaitingUserInput: boolean;
    } | null;
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
    create_presentations_state: null,
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

export const isSessionState = (value: unknown): value is SessionState =>
  typeof value === 'string' && SESSION_STATES.includes(value as SessionState);

export const isAskChappyMetadata = (value: unknown): value is AskChappyMetadata => {
  const isRecord = (input: unknown): input is Record<string, unknown> => input !== null && typeof input === 'object';

  if (!isRecord(value)) return false;
  const metadata = value;
  if ('expert_desk' in metadata || !('askchappy' in metadata)) return false;

  if (!isRecord(metadata.askchappy)) return false;
  const askchappy = metadata.askchappy;
  if (!isRecord(askchappy.context)) return false;
  const context = askchappy.context;

  return (
    askchappy.persona_id === 'ddn_chappy_vptm' &&
    askchappy.persona_label === 'Chappy' &&
    isSessionMode(askchappy.session_mode) &&
    askchappy.audience === 'partner_seller_or_se' &&
    (typeof askchappy.topic === 'string' || askchappy.topic === null) &&
    askchappy.desired_output === 'answer_questions_and_offer_guidance' &&
    (askchappy.create_presentations_state === null || (
      isRecord(askchappy.create_presentations_state) &&
      askchappy.create_presentations_state.active === true &&
      askchappy.create_presentations_state.mode === 'create_presentations' &&
      askchappy.create_presentations_state.step === 'intro' &&
      isRecord(askchappy.create_presentations_state.deckBrief) &&
      askchappy.create_presentations_state.deckBrief.schema_version === '1.0' &&
      askchappy.create_presentations_state.deckBrief.mode === 'create_presentations' &&
      askchappy.create_presentations_state.deckBrief.status === 'draft' &&
      Array.isArray(askchappy.create_presentations_state.messages) &&
      askchappy.create_presentations_state.messages.every((item) => typeof item === 'string') &&
      typeof askchappy.create_presentations_state.awaitingUserInput === 'boolean'
    )) &&
    context !== null &&
    typeof context === 'object' &&
    ['customer_name', 'partner_name', 'industry', 'use_case', 'competitor', 'meeting_goal'].every(
      (field) => field in context && (typeof context[field] === 'string' || context[field] === null),
    )
  );
};
