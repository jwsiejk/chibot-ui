import {
  CREATE_PRESENTATIONS_TECHNICAL_DEPTH,
  CREATE_PRESENTATIONS_TONES,
  type CreatePresentationsDeckBrief,
  type CreatePresentationsModeEvent,
  type CreatePresentationsOptionalField,
  type CreatePresentationsPptxThemeId,
} from '../../../../shared/contracts/createPresentationsMode';
import type { AskChappySession } from '../sessions/sessionStore';
import { appendGeneratedDeckHistory, toGeneratedDeckHistoryItem } from './createPresentationsDeckHistory';
import { generateOutlineFromBrief, renderOutlineReview, validateOutlineForApproval } from './createPresentationsOutlineFlow';

export type GuidedAssistantResponse = { text: string; spokenText?: string };

type DeckType = NonNullable<CreatePresentationsDeckBrief['deck_type']>;
type Tone = (typeof CREATE_PRESENTATIONS_TONES)[number];
type TechnicalDepth = (typeof CREATE_PRESENTATIONS_TECHNICAL_DEPTH)[number];
type RevisionField =
  | 'deck_type'
  | 'topic'
  | 'audience'
  | 'slide_count'
  | 'tone'
  | 'technical_depth'
  | 'must_include'
  | 'constraints'
  | 'required_messaging'
  | 'user_notes'
  | 'speaker_notes';
type CreatePresentationsState = NonNullable<AskChappySession['metadata']['askchappy']['create_presentations_state']>;
type RevisionCapableState = CreatePresentationsState & { pendingBriefRevisionField?: RevisionField };
type Choice<T extends string> = { label: string; value: T; aliases: string[] };
type PptxGenerationResult = { fileName: string; downloadUrl: string; generatedAt?: string; themeId?: CreatePresentationsPptxThemeId; filePath?: string };

const answer = (text: string, spokenText?: string): GuidedAssistantResponse => ({ text, spokenText });
const now = () => new Date().toISOString();
const nextEvent = (event: Omit<CreatePresentationsModeEvent, 'id' | 'ts'>): CreatePresentationsModeEvent => ({ id: `evt_${crypto.randomUUID()}`, ts: now(), ...event });
const normalizeAnswer = (input: string) => input.trim().toLowerCase().replace(/[.!?]+$/g, '').replace(/\s+/g, ' ');

const NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
  twentyone: 21,
  twentytwo: 22,
  twentythree: 23,
  twentyfour: 24,
  twentyfive: 25,
  twentysix: 26,
  twentyseven: 27,
  twentyeight: 28,
  twentynine: 29,
  thirty: 30,
};

const parseNumberChoice = (input: string): number | undefined => {
  const normalized = normalizeAnswer(input);
  const digit = normalized.match(/(?:option|choice|number)?\s*(\d{1,2})$/)?.[1];
  if (digit) return Number.parseInt(digit, 10);

  const compact = normalized.replace(/[-\s]/g, '');
  if (NUMBER_WORDS[compact]) return NUMBER_WORDS[compact];

  const word = normalized.match(/(?:option|choice|number)\s+([a-z\s-]+)/)?.[1]?.replace(/[-\s]/g, '');
  return word ? NUMBER_WORDS[word] : undefined;
};

const parseSlideCount = (input: string): number | undefined => {
  const parsed = parseNumberChoice(input) ?? Number.parseInt(normalizeAnswer(input).match(/\d+/)?.[0] ?? '', 10);
  return Number.isInteger(parsed) && parsed >= 3 && parsed <= 30 ? parsed : undefined;
};

const isSkipInput = (input: string) => new Set(['1', 'skip', 'none', 'no', 'n/a', 'not applicable', 'leave blank', 'blank', 'no thanks']).has(normalizeAnswer(input));
const isApprovalInput = (input: string) => /^(1|approve|approved|approve this brief|looks good|looks good to me|go ahead|yes|yes approve|no changes|no changes needed|good|continue)$/.test(normalizeAnswer(input));
const isRevisionInput = (input: string) => /^(approve with changes|revise|change|edit|update|fix|change something)$/i.test(normalizeAnswer(input));

const DECK_TYPE_CHOICES: Choice<DeckType>[] = [
  { label: 'Executive briefing', value: 'customer_executive_briefing', aliases: ['executive', 'executive briefing', 'customer executive briefing', 'exec briefing'] },
  { label: 'Technical deep dive', value: 'customer_technical_deep_dive', aliases: ['technical', 'technical deep dive', 'customer technical deep dive'] },
  { label: 'Partner enablement', value: 'partner_enablement', aliases: ['partner', 'partner enablement'] },
  { label: 'Internal training', value: 'internal_training', aliases: ['training', 'internal training'] },
  { label: 'Architecture review', value: 'architecture_review', aliases: ['architecture', 'architecture review'] },
  { label: 'Workshop', value: 'workshop', aliases: ['workshop'] },
  { label: 'Roadmap', value: 'roadmap', aliases: ['roadmap'] },
  { label: 'Proposal', value: 'proposal', aliases: ['proposal'] },
  { label: 'Custom', value: 'custom', aliases: ['custom'] },
];

const TONE_CHOICES: Choice<Tone>[] = [
  { label: 'Executive', value: 'executive', aliases: ['executive', 'exec'] },
  { label: 'Consultative', value: 'consultative', aliases: ['consultative'] },
  { label: 'Technical', value: 'technical', aliases: ['technical'] },
  { label: 'Technical but executive readable', value: 'technical_but_executive_readable', aliases: ['technical but executive readable', 'technical executive readable'] },
  { label: 'Sales', value: 'sales', aliases: ['sales'] },
  { label: 'Training', value: 'training', aliases: ['training'] },
  { label: 'Concise', value: 'concise', aliases: ['concise'] },
  { label: 'Custom', value: 'custom', aliases: ['custom'] },
];

const DEPTH_CHOICES: Choice<TechnicalDepth>[] = [
  { label: 'Low', value: 'low', aliases: ['low', 'light'] },
  { label: 'Medium', value: 'medium', aliases: ['medium', 'moderate'] },
  { label: 'High', value: 'high', aliases: ['high', 'deep'] },
  { label: 'Mixed', value: 'mixed', aliases: ['mixed'] },
];

const menuFromChoices = <T extends string>(title: string, choices: Choice<T>[]) => [
  title,
  ...choices.map((choice, index) => `${index + 1}. ${choice.label}`),
].join('\n');

const valueFromChoice = <T extends string>(input: string, choices: Choice<T>[]): T | undefined => {
  const selected = parseNumberChoice(input);
  if (selected && choices[selected - 1]) return choices[selected - 1].value;

  const normalized = normalizeAnswer(input);
  return choices.find((choice) => choice.aliases.some((alias) => normalizeAnswer(alias) === normalized))?.value;
};

const deckTypeFrom = (input: string): DeckType | undefined => valueFromChoice(input, DECK_TYPE_CHOICES);
const toneFrom = (input: string): Tone | undefined => valueFromChoice(input, TONE_CHOICES);
const depthFrom = (input: string): TechnicalDepth | undefined => valueFromChoice(input, DEPTH_CHOICES);

const speakerNotesFrom = (input: string): boolean | undefined => {
  const selected = parseNumberChoice(input);
  const normalized = normalizeAnswer(input);
  if (selected === 1 || normalized === 'yes' || normalized === 'y') return true;
  if (selected === 2 || normalized === 'no' || normalized === 'n') return false;
  return undefined;
};

const deckTypeMenu = menuFromChoices('Choose one of the options below:', DECK_TYPE_CHOICES);
const toneMenu = menuFromChoices('Choose a tone:', TONE_CHOICES);
const depthMenu = menuFromChoices('Choose technical depth:', DEPTH_CHOICES);
const speakerNotesMenu = ['Include speaker notes?', '1. Yes', '2. No'].join('\n');
const revisionMenuText = [
  'What do you want to revise?',
  '1. Deck type',
  '2. Topic',
  '3. Audience',
  '4. Slide count',
  '5. Tone',
  '6. Technical depth',
  '7. Must-include sections',
  '8. Constraints',
  '9. Required messaging',
  '10. User notes',
  '11. Speaker notes',
].join('\n');

const revisionFieldByChoice: Record<number, RevisionField> = {
  1: 'deck_type',
  2: 'topic',
  3: 'audience',
  4: 'slide_count',
  5: 'tone',
  6: 'technical_depth',
  7: 'must_include',
  8: 'constraints',
  9: 'required_messaging',
  10: 'user_notes',
  11: 'speaker_notes',
};

const revisionFieldLabels: Record<RevisionField, string> = {
  deck_type: 'Deck type',
  topic: 'Topic',
  audience: 'Audience',
  slide_count: 'Slide count',
  tone: 'Tone',
  technical_depth: 'Technical depth',
  must_include: 'Must-include sections',
  constraints: 'Constraints',
  required_messaging: 'Required messaging',
  user_notes: 'User notes',
  speaker_notes: 'Speaker notes',
};

const promptByRevisionField: Record<RevisionField, string> = {
  deck_type: deckTypeMenu,
  topic: 'What should the topic be?',
  audience: 'Who is the audience?',
  slide_count: 'What should slide count be? (3–30)',
  tone: toneMenu,
  technical_depth: depthMenu,
  must_include: 'What should must-include sections be? (comma-separated)',
  constraints: 'What should constraints be? (comma-separated)',
  required_messaging: 'What should required messaging be? (comma-separated)',
  user_notes: 'What should user notes be?',
  speaker_notes: speakerNotesMenu,
};

const labelForValue = (value: string | undefined): string => {
  if (!value) return 'Skipped';
  return [...DECK_TYPE_CHOICES, ...TONE_CHOICES, ...DEPTH_CHOICES].find((choice) => choice.value === value)?.label ?? value;
};

const listFromInput = (input: string) => input.split(',').map((item) => item.trim()).filter(Boolean);

const valueAfterField = (input: string, fieldPattern: string): string => input.replace(new RegExp(`^.*?${fieldPattern}\\s*(?:to|as)?\\s*`, 'i'), '').trim();

const applyBriefFieldValue = (brief: CreatePresentationsDeckBrief, field: RevisionField, value: string): boolean => {
  if (field === 'deck_type') {
    const mapped = deckTypeFrom(value);
    if (!mapped) return false;
    brief.deck_type = mapped;
    return true;
  }
  if (field === 'topic') {
    if (!value.trim()) return false;
    brief.topic = value.trim();
    return true;
  }
  if (field === 'audience') {
    if (!value.trim()) return false;
    brief.audience = value.trim();
    return true;
  }
  if (field === 'slide_count') {
    const slideCount = parseSlideCount(value);
    if (!slideCount) return false;
    brief.slide_count = slideCount;
    return true;
  }
  if (field === 'tone') {
    const mapped = toneFrom(value);
    if (!mapped) return false;
    brief.tone = mapped;
    return true;
  }
  if (field === 'technical_depth') {
    const mapped = depthFrom(value);
    if (!mapped) return false;
    brief.technical_depth = mapped;
    return true;
  }
  if (field === 'must_include') {
    brief.must_include = listFromInput(value);
    return true;
  }
  if (field === 'constraints') {
    brief.constraints = listFromInput(value);
    return true;
  }
  if (field === 'required_messaging') {
    brief.required_messaging = listFromInput(value);
    return true;
  }
  if (field === 'user_notes') {
    brief.user_notes = value.trim();
    return true;
  }

  const speakerNotes = speakerNotesFrom(value);
  if (typeof speakerNotes !== 'boolean') return false;
  brief.output.speaker_notes = speakerNotes;
  return true;
};

const applyBriefRevisionFromText = (brief: CreatePresentationsDeckBrief, input: string): RevisionField | undefined => {
  const normalized = normalizeAnswer(input);

  if (/(^|\s)(set|change|update).*deck type/.test(normalized)) {
    return applyBriefFieldValue(brief, 'deck_type', valueAfterField(input, 'deck type')) ? 'deck_type' : undefined;
  }
  if (/(^|\s)(set|change|update).*topic/.test(normalized)) {
    return applyBriefFieldValue(brief, 'topic', valueAfterField(input, 'topic')) ? 'topic' : undefined;
  }
  if (/(^|\s)(set|change|update).*audience/.test(normalized)) {
    return applyBriefFieldValue(brief, 'audience', valueAfterField(input, 'audience')) ? 'audience' : undefined;
  }
  if (/(^|\s)(set|change|update).*(slide count|slides)/.test(normalized)) {
    return applyBriefFieldValue(brief, 'slide_count', input) ? 'slide_count' : undefined;
  }
  if (/(^|\s)(set|change|update).*tone/.test(normalized)) {
    return applyBriefFieldValue(brief, 'tone', valueAfterField(input, 'tone')) ? 'tone' : undefined;
  }
  if (/(^|\s)(set|change|update).*(technical depth|depth)/.test(normalized)) {
    return applyBriefFieldValue(brief, 'technical_depth', valueAfterField(input, '(?:technical depth|depth)')) ? 'technical_depth' : undefined;
  }
  if (/(^|\s)(set|change|update).*(must-include|must include)/.test(normalized)) {
    return applyBriefFieldValue(brief, 'must_include', valueAfterField(input, '(?:must-include|must include)(?: sections?)?')) ? 'must_include' : undefined;
  }
  if (/(^|\s)(set|change|update).*constraints?/.test(normalized)) {
    return applyBriefFieldValue(brief, 'constraints', valueAfterField(input, 'constraints?')) ? 'constraints' : undefined;
  }
  if (/(^|\s)(set|change|update).*(required messaging|messaging)/.test(normalized)) {
    return applyBriefFieldValue(brief, 'required_messaging', valueAfterField(input, '(?:required messaging|messaging)(?: points?)?')) ? 'required_messaging' : undefined;
  }
  if (/(^|\s)(set|change|update).*user notes/.test(normalized)) {
    return applyBriefFieldValue(brief, 'user_notes', valueAfterField(input, 'user notes')) ? 'user_notes' : undefined;
  }
  if (/(^|\s)(set|change|update).*speaker notes/.test(normalized)) {
    return applyBriefFieldValue(brief, 'speaker_notes', valueAfterField(input, 'speaker notes')) ? 'speaker_notes' : undefined;
  }

  return undefined;
};

export const handleCreatePresentationsTurn = async (session: AskChappySession): Promise<GuidedAssistantResponse> => {
  const state = session.metadata.askchappy.create_presentations_state as RevisionCapableState | null;
  if (!state) throw new Error('create_presentations_state missing while in create_presentations mode.');

  const brief = state.deckBrief;
  state.generatedDeckHistory ??= [];

  const userText = [...session.transcript].reverse().find((m) => m.role === 'user')?.text?.trim() ?? '';
  const normalized = normalizeAnswer(userText);
  state.events.push(nextEvent({ actor: 'user', step: state.step, kind: 'answer_recorded', text: userText }));
  const generatePptxRuntime = async (): Promise<PptxGenerationResult> => {
    if (typeof window !== 'undefined') {
      const response = await fetch('/api/presentations/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: session.session_id, brief, outline: state.outline }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload?.error === 'string' ? payload.error : 'Presentation generation failed.');
      return { fileName: payload.fileName as string, downloadUrl: payload.downloadUrl as string, generatedAt: payload.generatedAt as string | undefined, themeId: payload.themeId as CreatePresentationsPptxThemeId | undefined, filePath: undefined };
    }
    const { generatePptxFromApprovedOutline } = await import('./createPresentationsPptxGenerator');
    return generatePptxFromApprovedOutline(session.session_id, brief, state.outline);
  };

  const ask = (text: string, spoken?: string) => {
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'question_asked', text }));
    return answer(text, spoken);
  };

  const validationError = (text: string, spoken?: string) => {
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'validation_error', text }));
    return ask(text, spoken);
  };

  const markSkipped = (field: CreatePresentationsOptionalField) => {
    if (!state.skippedFields.includes(field)) state.skippedFields.push(field);
  };

  const renderValue = (value: unknown) => {
    if (Array.isArray(value)) return value.length ? value.join(', ') : 'Skipped';
    return value ?? 'Skipped';
  };

  const renderBriefReview = () => [
    'Here’s the brief I heard:',
    `1. Deck type: ${labelForValue(brief.deck_type)}`,
    `2. Topic: ${brief.topic}`,
    `3. Audience: ${brief.audience}`,
    `4. Customer context: ${renderValue(brief.customer_context)}`,
    `5. Industry: ${renderValue(brief.industry)}`,
    `6. Primary use case: ${renderValue(brief.use_case)}`,
    `7. Slide count: ${brief.slide_count}`,
    `8. Tone: ${labelForValue(brief.tone)}`,
    `9. Technical depth: ${labelForValue(brief.technical_depth)}`,
    `10. Must-include sections: ${renderValue(brief.must_include)}`,
    `11. Constraints: ${renderValue(brief.constraints)}`,
    `12. Required messaging: ${renderValue(brief.required_messaging)}`,
    `13. User notes: ${renderValue(brief.user_notes)}`,
    `14. Speaker notes: ${brief.output.speaker_notes ? 'Yes' : 'No'}`,
    '',
    'Next:',
    '1. This is correct — generate the outline',
    '2. Edit the brief',
  ].join('\n');

  const presentBriefReview = (spoken = 'If this looks correct, choose 1. To edit, choose 2.') => {
    brief.status = 'brief_review';
    state.step = 'brief_review';
    const review = renderBriefReview();
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_review_presented', text: review }));
    return ask(review, spoken);
  };

  const pushBriefUpdated = (field: RevisionField) => {
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_updated', field }));
  };

  if (state.step === 'intro') state.step = 'collecting_brief';

  if (state.step === 'brief_review') {
    if (isApprovalInput(userText)) {
      brief.status = 'outline_review';
      state.outline = generateOutlineFromBrief(brief, now());
      state.step = 'outline_review';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_approved', text: 'Deck Brief approved.' }));
      return ask(`Great — I approved the brief and generated the outline. Review it below.\n\n${renderOutlineReview(state.outline)}`, 'Great — I generated the outline. Review it below.');
    }

    if (state.pendingBriefRevisionField) {
      const field = state.pendingBriefRevisionField;
      if (!applyBriefFieldValue(brief, field, userText)) {
        return validationError(`I couldn't update that yet. Please provide a valid value for ${revisionFieldLabels[field]}.`);
      }
      state.pendingBriefRevisionField = undefined;
      pushBriefUpdated(field);
      return presentBriefReview('Here’s the updated brief.');
    }

    const directRevision = applyBriefRevisionFromText(brief, userText);
    if (directRevision) {
      state.pendingBriefRevisionField = undefined;
      pushBriefUpdated(directRevision);
      return presentBriefReview('Here’s the updated brief.');
    }

    const choice = parseNumberChoice(userText);
    if (choice && revisionFieldByChoice[choice]) {
      state.pendingBriefRevisionField = revisionFieldByChoice[choice];
      return ask(promptByRevisionField[revisionFieldByChoice[choice]], 'Tell me the updated value.');
    }

    if (normalized === '2') return ask(revisionMenuText, 'Choose what you want to revise.');
    if (normalized === 'user_notes') return ask('What should user notes be? Type the new value, or choose 1 to skip.');
    if (isRevisionInput(userText)) return ask(revisionMenuText, 'Choose what you want to revise.');

    return ask(revisionMenuText, 'Choose what you want to revise.');
  }

  if (state.step === 'outline_review') {
    const outlineChoice = parseNumberChoice(userText);
    const wantsCreatePowerPoint =
      outlineChoice === 1
      || /^(approve|approve outline|yes|looks good|no changes|continue|one)$/i.test(normalized)
      || /(approve and create|approve.*powerpoint|approve.*pptx|create powerpoint|generate presentation|generate pptx|export pptx)/i.test(userText);

    if (wantsCreatePowerPoint) {
      const errors = validateOutlineForApproval(state.outline, brief);
      if (errors.length) return validationError(`I cannot approve this outline yet:\n- ${errors.join('\\n- ')}`);

      state.outline.status = 'outline_approved';
      brief.status = 'outline_approved';
      state.step = 'outline_approved';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'outline_approved', text: 'Outline approved.' }));

      state.generatedPresentation = { status: 'generating', format: 'pptx' };
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generation_requested', text: userText }));

      try {
        const result = await generatePptxRuntime();

        state.generatedPresentation = {
          status: 'generated',
          format: 'pptx',
          file_name: result.fileName,
          download_url: result.downloadUrl,
          generated_at: result.generatedAt,
          theme_id: result.themeId,
          ...(result.filePath ? { file_path: result.filePath } : {}),
        };

        const history = toGeneratedDeckHistoryItem(state.generatedPresentation, brief, state.outline);
        if (history) state.generatedDeckHistory = appendGeneratedDeckHistory(state.generatedDeckHistory, history);

        state.step = 'presentation_generated';
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generated', text: result.downloadUrl }));

        return ask(`Your PowerPoint is ready: ${result.downloadUrl}`);
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown PPTX generation error.';
        state.generatedPresentation = { status: 'error', format: 'pptx', error_message: message };
        state.step = 'error';
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generation_failed', text: message }));
        return ask(`I could not generate the PowerPoint: ${message}`);
      }
    }

    if (outlineChoice === 2 || /^(edit|revise|revise outline|change outline|change something)$/i.test(normalized)) {
      return ask(
        'Tell me what to change in the outline. For example: change slide 2 title to ...',
        'Tell me what to change in the outline.',
      );
    }

    return ask(
      'Deck Outline Review complete. Next:\n1. This is correct — create the PowerPoint\n2. Edit the outline',
      'If this outline looks correct, choose 1 to create the PowerPoint. To edit, choose 2.',
    );
  }


  if (state.step === 'outline_approved' || state.step === 'presentation_generated') {
    if (!(parseNumberChoice(userText) === 1 || /(create powerpoint|generate presentation|generate pptx|export pptx)/i.test(userText))) {
      return ask('Outline approved. Next:\n1. This is correct — create the PowerPoint\n2. Edit the outline', 'If this outline looks correct, choose 1 to create the PowerPoint. To edit, choose 2.');
    }
    state.generatedPresentation = { status: 'generating', format: 'pptx' };
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generation_requested', text: userText }));
    try {
      const result = await generatePptxRuntime();
      state.generatedPresentation = { status: 'generated', format: 'pptx', file_name: result.fileName, download_url: result.downloadUrl, generated_at: result.generatedAt, theme_id: result.themeId, ...(result.filePath ? { file_path: result.filePath } : {}) };
      const history = toGeneratedDeckHistoryItem(state.generatedPresentation, brief, state.outline);
      if (history) state.generatedDeckHistory = appendGeneratedDeckHistory(state.generatedDeckHistory, history);
      state.step = 'presentation_generated';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generated', text: result.downloadUrl }));
      return ask(`Your PowerPoint is ready: ${result.downloadUrl}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown PPTX generation error.';
      state.generatedPresentation = { status: 'error', format: 'pptx', error_message: message };
      state.step = 'error';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'pptx_generation_failed', text: message }));
      return ask(`I could not generate the PowerPoint: ${message}`);
    }
  }

  if (!brief.deck_type) {
    const mapped = deckTypeFrom(userText);
    if (!mapped) return validationError(deckTypeMenu, 'Choose one of the options below.');
    brief.deck_type = mapped;
  } else if (!brief.topic) {
    brief.topic = userText;
  } else if (!brief.audience) {
    brief.audience = userText;
  } else if (!brief.customer_context && !state.skippedFields.includes('customer_context')) {
    if (isSkipInput(userText)) markSkipped('customer_context');
    else if (normalized === 'yes') return ask('What should I include? Type it, or choose 1 to skip.');
    else brief.customer_context = userText;
  } else if (!brief.industry && !state.skippedFields.includes('industry')) {
    if (isSkipInput(userText)) markSkipped('industry');
    else brief.industry = userText;
  } else if (!brief.use_case && !state.skippedFields.includes('use_case')) {
    if (isSkipInput(userText)) markSkipped('use_case');
    else brief.use_case = userText;
  } else if (typeof brief.slide_count !== 'number') {
    const slideCount = parseSlideCount(userText);
    if (!slideCount) return validationError('Slide count must be an integer between 3 and 30.');
    brief.slide_count = slideCount;
  } else if (!brief.tone) {
    const tone = toneFrom(userText);
    if (!tone) return validationError(toneMenu, 'Choose a tone from the list below.');
    brief.tone = tone;
  } else if (!brief.technical_depth) {
    const depth = depthFrom(userText);
    if (!depth) return validationError(depthMenu, 'Choose the technical depth below.');
    brief.technical_depth = depth;
  } else if (!brief.must_include && !state.skippedFields.includes('must_include')) {
    if (isSkipInput(userText)) markSkipped('must_include');
    else if (/scared|afraid|nervous/i.test(userText)) return ask('No problem — we can keep this simple. Type a comma-separated list, or choose 1 to skip.');
    else brief.must_include = listFromInput(userText);
  } else if (!brief.constraints && !state.skippedFields.includes('constraints')) {
    if (isSkipInput(userText)) markSkipped('constraints');
    else brief.constraints = listFromInput(userText);
  } else if (!brief.required_messaging && !state.skippedFields.includes('required_messaging')) {
    if (isSkipInput(userText)) markSkipped('required_messaging');
    else brief.required_messaging = listFromInput(userText);
  } else if (!brief.user_notes && !state.skippedFields.includes('user_notes')) {
    if (isSkipInput(userText)) markSkipped('user_notes');
    else brief.user_notes = userText;
  } else if (typeof brief.output.speaker_notes !== 'boolean') {
    const speakerNotes = speakerNotesFrom(userText);
    if (typeof speakerNotes !== 'boolean') return validationError(speakerNotesMenu, 'Choose yes or no below.');
    brief.output.speaker_notes = speakerNotes;
  }

  const next = !brief.topic
    ? 'What is the presentation topic?'
    : !brief.audience
      ? 'Who is the audience?'
      : !brief.customer_context && !state.skippedFields.includes('customer_context')
        ? 'Customer/company context? Type it, or choose 1 to skip.'
        : !brief.industry && !state.skippedFields.includes('industry')
          ? 'Industry? Type it, or choose 1 to skip.'
          : !brief.use_case && !state.skippedFields.includes('use_case')
            ? 'Primary use case? Type it, or choose 1 to skip.'
            : typeof brief.slide_count !== 'number'
              ? 'How many slides do you want (3–30)?'
              : !brief.tone
                ? toneMenu
                : !brief.technical_depth
                  ? depthMenu
                  : !brief.must_include && !state.skippedFields.includes('must_include')
                    ? 'Must-include sections? Type a comma-separated list, or choose 1 to skip.'
                    : !brief.constraints && !state.skippedFields.includes('constraints')
                      ? 'Constraints? Type a comma-separated list, or choose 1 to skip.'
                      : !brief.required_messaging && !state.skippedFields.includes('required_messaging')
                        ? 'Required messaging points? Type a comma-separated list, or choose 1 to skip.'
                        : !brief.user_notes && !state.skippedFields.includes('user_notes')
                          ? 'Extra user notes? Type notes, or choose 1 to skip.'
                          : typeof brief.output.speaker_notes !== 'boolean'
                            ? speakerNotesMenu
                            : '';

  if (next) {
    return ask(next, next === toneMenu ? 'Choose a tone from the list below.' : next === depthMenu ? 'Choose the technical depth below.' : next === speakerNotesMenu ? 'Choose yes or no below.' : undefined);
  }

  return presentBriefReview();
};
