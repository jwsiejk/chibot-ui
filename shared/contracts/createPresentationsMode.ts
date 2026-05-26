export const CREATE_PRESENTATIONS_INTRO_MESSAGE =
  "Create Presentations Mode is ready. I’ll walk you through building a professional deck step by step. What kind of presentation are we creating?";

export const CREATE_PRESENTATIONS_DECK_TYPES = [
  'customer_executive_briefing',
  'customer_technical_deep_dive',
  'partner_enablement',
  'internal_training',
  'architecture_review',
  'workshop',
  'roadmap',
  'proposal',
  'custom',
] as const;

export const CREATE_PRESENTATIONS_TONES = [
  'executive',
  'consultative',
  'technical',
  'technical_but_executive_readable',
  'sales',
  'training',
  'concise',
  'custom',
] as const;

export const CREATE_PRESENTATIONS_TECHNICAL_DEPTH = ['low', 'medium', 'high', 'mixed'] as const;

export const CREATE_PRESENTATIONS_STEPS = ['intro', 'collecting_brief', 'brief_review', 'brief_approved', 'outline_review', 'outline_approved', 'error'] as const;

export type CreatePresentationsStep = (typeof CREATE_PRESENTATIONS_STEPS)[number];

export const CREATE_PRESENTATIONS_EVENT_KINDS = [
  'mode_entered',
  'question_asked',
  'answer_recorded',
  'validation_error',
  'brief_updated',
  'brief_review_presented',
  'brief_approved',
  'outline_generation_requested',
  'outline_generated',
  'outline_review_presented',
  'outline_revision_requested',
  'outline_updated',
  'outline_approved',
  'mode_exited',
] as const;

export type CreatePresentationsModeEvent = {
  id: string;
  ts: string;
  actor: 'user' | 'assistant' | 'system';
  step: CreatePresentationsStep;
  kind: (typeof CREATE_PRESENTATIONS_EVENT_KINDS)[number];
  text?: string;
  field?: string;
  value?: unknown;
};

export type CreatePresentationsDeckBrief = {
  schema_version: '1.0';
  mode: 'create_presentations';
  deck_type?: (typeof CREATE_PRESENTATIONS_DECK_TYPES)[number];
  topic?: string;
  audience?: string;
  customer_context?: string;
  industry?: string;
  use_case?: string;
  slide_count?: number;
  tone?: (typeof CREATE_PRESENTATIONS_TONES)[number];
  technical_depth?: (typeof CREATE_PRESENTATIONS_TECHNICAL_DEPTH)[number];
  must_include?: string[];
  user_notes?: string;
  constraints?: string[];
  required_messaging?: string[];
  source_requirements: {
    source_policy: 'user_provided_only';
    citations_required: false;
    allowed_source_types: ['manual_notes'];
  };
  output: {
    format: 'pptx';
    speaker_notes?: boolean;
  };
  status: 'draft' | 'brief_review' | 'brief_approved' | 'outline_draft' | 'outline_review' | 'outline_approved' | 'error';
};

export type CreatePresentationsOutlineSlide = {
  slide_number: number;
  title: string;
  objective: string;
  key_points: string[];
  speaker_notes_prompt?: string;
};

export type CreatePresentationsOutlineState = {
  status: 'not_started' | 'outline_draft' | 'outline_review' | 'outline_approved' | 'error';
  slides: CreatePresentationsOutlineSlide[];
  created_at?: string;
  updated_at?: string;
};

export const CREATE_PRESENTATIONS_OPTIONAL_FIELDS = [
  'customer_context',
  'industry',
  'use_case',
  'must_include',
  'constraints',
  'required_messaging',
  'user_notes',
] as const;

export type CreatePresentationsOptionalField = (typeof CREATE_PRESENTATIONS_OPTIONAL_FIELDS)[number];

export const createPresentationsModeState = () => ({
  active: true as const,
  mode: 'create_presentations' as const,
  step: 'intro' as const,
  deckBrief: {
    schema_version: '1.0' as const,
    mode: 'create_presentations' as const,
    source_requirements: {
      source_policy: 'user_provided_only' as const,
      citations_required: false as const,
      allowed_source_types: ['manual_notes'] as ['manual_notes'],
    },
    output: { format: 'pptx' as const },
    status: 'draft' as const,
  },
  events: [
    {
      id: `evt_${crypto.randomUUID()}`,
      ts: new Date().toISOString(),
      actor: 'system' as const,
      step: 'intro' as const,
      kind: 'mode_entered' as const,
      text: CREATE_PRESENTATIONS_INTRO_MESSAGE,
    },
  ],
  outline: {
    status: 'not_started' as const,
    slides: [] as CreatePresentationsOutlineSlide[],
  },
  skippedFields: [] as CreatePresentationsOptionalField[],
  awaitingUserInput: true,
});
