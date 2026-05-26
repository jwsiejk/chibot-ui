import {
  CREATE_PRESENTATIONS_DECK_TYPES,
  CREATE_PRESENTATIONS_TECHNICAL_DEPTH,
  CREATE_PRESENTATIONS_TONES,
  type CreatePresentationsDeckBrief,
  type CreatePresentationsModeEvent,
  type CreatePresentationsOptionalField,
} from '../../../../shared/contracts/createPresentationsMode';
import type { AskChappySession } from '../sessions/sessionStore';
import {
  generateOutlineFromBrief,
  isOutlineApproval,
  isOutlineGenerationRequest,
  isOutlineRegenerateRequest,
  renderOutlineReview,
  validateOutlineForApproval,
} from './createPresentationsOutlineFlow';

const nextEvent = (
  event: Omit<CreatePresentationsModeEvent, 'id' | 'ts'>,
): CreatePresentationsModeEvent => ({
  id: `evt_${crypto.randomUUID()}`,
  ts: new Date().toISOString(),
  ...event,
});

const toList = (input: string) =>
  input
    .split(',')
    .map((i) => i.trim())
    .filter(Boolean);

const normalize = (value: string) => value.trim().toLowerCase();
const yes = (t: string) => /^(yes|y|true)$/i.test(t.trim());
const no = (t: string) => /^(no|n|false)$/i.test(t.trim());
const approveBrief = (t: string) => /^(approve|approved|looks good|go ahead|yes,? approve)$/i.test(t.trim());
const now = () => new Date().toISOString();

const SKIP_INPUTS = new Set(['skip', 'none', 'no', 'n/a', 'not applicable', 'leave blank', 'blank']);
const isSkipInput = (input: string) => SKIP_INPUTS.has(normalize(input));

const deckTypeMap: Record<string, (typeof CREATE_PRESENTATIONS_DECK_TYPES)[number]> = {
  'executive briefing': 'customer_executive_briefing',
  'customer executive briefing': 'customer_executive_briefing',
  'exec briefing': 'customer_executive_briefing',
  'technical deep dive': 'customer_technical_deep_dive',
  'customer technical deep dive': 'customer_technical_deep_dive',
  'partner enablement': 'partner_enablement',
  training: 'internal_training',
  'internal training': 'internal_training',
  'architecture review': 'architecture_review',
  workshop: 'workshop',
  roadmap: 'roadmap',
  proposal: 'proposal',
  custom: 'custom',
};

const toneMap: Record<string, (typeof CREATE_PRESENTATIONS_TONES)[number]> = {
  exec: 'executive',
  executive: 'executive',
  consultative: 'consultative',
  technical: 'technical',
  'technical but executive readable': 'technical_but_executive_readable',
  'technical executive readable': 'technical_but_executive_readable',
  sales: 'sales',
  training: 'training',
  concise: 'concise',
  custom: 'custom',
};

const depthMap: Record<string, (typeof CREATE_PRESENTATIONS_TECHNICAL_DEPTH)[number]> = {
  low: 'low',
  light: 'low',
  medium: 'medium',
  moderate: 'medium',
  high: 'high',
  deep: 'high',
  mixed: 'mixed',
};

const findMissingRequired = (brief: CreatePresentationsDeckBrief) => {
  const missing: string[] = [];

  if (!brief.deck_type) missing.push('deck_type');
  if (!brief.topic) missing.push('topic');
  if (!brief.audience) missing.push('audience');
  if (
    typeof brief.slide_count !== 'number'
    || !Number.isInteger(brief.slide_count)
    || brief.slide_count < 3
    || brief.slide_count > 30
  ) {
    missing.push('slide_count');
  }
  if (!brief.tone) missing.push('tone');
  if (!brief.technical_depth) missing.push('technical_depth');
  if (typeof brief.output.speaker_notes !== 'boolean') missing.push('output.speaker_notes');

  return missing;
};

export const handleCreatePresentationsTurn = (session: AskChappySession): string => {
  const state = session.metadata.askchappy.create_presentations_state;
  if (!state) {
    throw new Error('create_presentations_state missing while in create_presentations mode.');
  }

  const userText = [...session.transcript].reverse().find((m) => m.role === 'user')?.text?.trim() ?? '';
  state.events.push(nextEvent({ actor: 'user', step: state.step, kind: 'answer_recorded', text: userText }));

  const brief = state.deckBrief;

  const ask = (text: string) => {
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'question_asked', text }));
    return text;
  };

  const mapEnum = <T extends string>(value: string, map: Record<string, T>, field: string): T | undefined => {
    const raw = normalize(value);

    if (map[raw]) {
      return map[raw];
    }

    if ((Object.values(map) as string[]).includes(raw)) {
      return raw as T;
    }

    state.events.push(
      nextEvent({
        actor: 'assistant',
        step: state.step,
        kind: 'validation_error',
        field,
        text: `Unmapped ${field} value.`,
      }),
    );
    return undefined;
  };

  const presentOutlineReview = () => {
    const review = renderOutlineReview(state.outline);
    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'outline_review_presented', text: review }));
    return review;
  };

  const generateOutline = () => {
    if (!(brief.status === 'brief_approved' || state.step === 'outline_review')) {
      return ask('Outline generation is only available after Deck Brief approval.');
    }

    state.events.push(
      nextEvent({
        actor: 'user',
        step: state.step,
        kind: 'outline_generation_requested',
        text: userText,
      }),
    );

    state.outline = generateOutlineFromBrief(brief, now());
    // outline_draft remains a documented lifecycle concept, but Phase 3 generation + presentation is synchronous.
    brief.status = 'outline_review';
    state.step = 'outline_review';

    state.events.push(
      nextEvent({
        actor: 'assistant',
        step: state.step,
        kind: 'outline_generated',
        text: 'Outline generated from approved Deck Brief.',
      }),
    );

    return presentOutlineReview();
  };

  if (state.step === 'intro') {
    state.step = 'collecting_brief';
  }

  if (state.step === 'outline_review') {
    if (isOutlineApproval(userText)) {
      const errors = validateOutlineForApproval(state.outline, brief);
      if (errors.length) {
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'validation_error', text: errors.join(' | ') }));
        return ask(`I cannot approve this outline yet:\n- ${errors.join('\n- ')}`);
      }

      state.outline.status = 'outline_approved';
      state.outline.updated_at = now();
      brief.status = 'outline_approved';
      state.step = 'outline_approved';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'outline_approved', text: 'Outline approved.' }));
      return 'Great—your outline is approved. Phase 3 is complete. No PPTX generation happens in this phase.';
    }

    if (isOutlineRegenerateRequest(userText)) {
      return generateOutline();
    }

    const titleMatch = userText.match(/change\s+slide\s+(\d+)\s+title\s+to\s+(.+)/i);
    const objectiveMatch = userText.match(/change\s+slide\s+(\d+)\s+objective\s+to\s+(.+)/i);
    const replacePointsMatch = userText.match(/change\s+slide\s+(\d+)\s+key\s+points\s+to\s+(.+)/i);
    const addPointMatch = userText.match(/add\s+key\s+point\s+to\s+slide\s+(\d+)\s*:\s*(.+)/i);
    const removePointMatch = userText.match(/remove\s+key\s+point\s+from\s+slide\s+(\d+)\s*:\s*(.+)/i);

    const parseSlide = (raw: string) => Number.parseInt(raw, 10) - 1;
    const slideAt = (idx: number) => state.outline.slides[idx];
    const invalidSlide = (idx: number) => idx < 0 || idx >= state.outline.slides.length;

    const updateOk = (field: string, value: unknown) => {
      state.outline.status = 'outline_review';
      state.outline.updated_at = now();
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'outline_updated', field, value }));
      return presentOutlineReview();
    };

    if (titleMatch) {
      const idx = parseSlide(titleMatch[1]);
      if (invalidSlide(idx)) {
        return ask(`Slide ${titleMatch[1]} is out of range. Choose a slide between 1 and ${state.outline.slides.length}.`);
      }
      slideAt(idx).title = titleMatch[2].trim();
      return updateOk('slide.title', { slide: idx + 1, title: slideAt(idx).title });
    }

    if (objectiveMatch) {
      const idx = parseSlide(objectiveMatch[1]);
      if (invalidSlide(idx)) {
        return ask(`Slide ${objectiveMatch[1]} is out of range. Choose a slide between 1 and ${state.outline.slides.length}.`);
      }
      slideAt(idx).objective = objectiveMatch[2].trim();
      return updateOk('slide.objective', { slide: idx + 1, objective: slideAt(idx).objective });
    }

    if (replacePointsMatch) {
      const idx = parseSlide(replacePointsMatch[1]);
      if (invalidSlide(idx)) {
        return ask(`Slide ${replacePointsMatch[1]} is out of range. Choose a slide between 1 and ${state.outline.slides.length}.`);
      }

      const points = toList(replacePointsMatch[2]);
      if (points.length < 2 || points.length > 5) {
        return ask('Key points must contain 2–5 items.');
      }

      slideAt(idx).key_points = points;
      return updateOk('slide.key_points', { slide: idx + 1, key_points: points });
    }

    if (addPointMatch) {
      const idx = parseSlide(addPointMatch[1]);
      if (invalidSlide(idx)) {
        return ask(`Slide ${addPointMatch[1]} is out of range. Choose a slide between 1 and ${state.outline.slides.length}.`);
      }
      if (slideAt(idx).key_points.length >= 5) {
        return ask('This slide already has 5 key points. Remove one before adding another.');
      }

      const point = addPointMatch[2].trim();
      slideAt(idx).key_points.push(point);
      return updateOk('slide.key_points_add', { slide: idx + 1, key_point: point });
    }

    if (removePointMatch) {
      const idx = parseSlide(removePointMatch[1]);
      if (invalidSlide(idx)) {
        return ask(`Slide ${removePointMatch[1]} is out of range. Choose a slide between 1 and ${state.outline.slides.length}.`);
      }

      const text = removePointMatch[2].trim();
      const filtered = slideAt(idx).key_points.filter((point) => point !== text);

      if (filtered.length === slideAt(idx).key_points.length) {
        return ask(`I could not find that exact key point on slide ${idx + 1}.`);
      }
      if (filtered.length < 2) {
        return ask('Each slide must keep at least 2 key points.');
      }

      slideAt(idx).key_points = filtered;
      return updateOk('slide.key_points_remove', { slide: idx + 1, key_point: text });
    }

    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'outline_revision_requested', text: userText }));
    return ask('Please clarify the outline revision. Supported changes: change slide N title/objective, replace key points, add/remove key point, regenerate outline, or approve outline.');
  }

  if (state.step === 'brief_approved') {
    if (isOutlineGenerationRequest(userText)) {
      return generateOutline();
    }
    return ask("Your Deck Brief is approved. Say 'generate outline' when you’re ready, and I’ll create the proposed slide flow.");
  }

  const renderValue = (field: CreatePresentationsOptionalField) => {
    if (state.skippedFields.includes(field)) return 'Skipped';

    const value = brief[field];
    if (Array.isArray(value)) return value.length ? value.join(', ') : 'Skipped';
    return value ?? 'Skipped';
  };

  const presentReview = () => {
    brief.status = 'brief_review';
    state.step = 'brief_review';

    const review = [
      'Deck Brief Review',
      `- deck_type: ${brief.deck_type}`,
      `- topic: ${brief.topic}`,
      `- audience: ${brief.audience}`,
      `- customer_context: ${renderValue('customer_context')}`,
      `- industry: ${renderValue('industry')}`,
      `- use_case: ${renderValue('use_case')}`,
      `- slide_count: ${brief.slide_count}`,
      `- tone: ${brief.tone}`,
      `- technical_depth: ${brief.technical_depth}`,
      `- must_include: ${renderValue('must_include')}`,
      `- constraints: ${renderValue('constraints')}`,
      `- required_messaging: ${renderValue('required_messaging')}`,
      `- user_notes: ${renderValue('user_notes')}`,
      `- output.speaker_notes: ${brief.output.speaker_notes ? 'yes' : 'no'}`,
      '- source policy: user_provided_only',
      'Approve this brief, or tell me what to revise.',
    ].join('\n');

    state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_review_presented', text: review }));
    return review;
  };

  const markSkipped = (field: CreatePresentationsOptionalField) => {
    if (!state.skippedFields.includes(field)) {
      state.skippedFields.push(field);
    }
  };

  if (state.step === 'brief_review') {
    if (approveBrief(userText)) {
      const missing = findMissingRequired(brief);
      if (missing.length) {
        return ask(`I cannot approve yet. Please revise required fields: ${missing.join(', ')}.`);
      }

      brief.status = 'brief_approved';
      state.step = 'brief_approved';
      state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_approved', text: 'Deck Brief approved.' }));
      return "Great—your Deck Brief is approved. Say 'generate outline' when you’re ready, and I’ll create the proposed slide flow.";
    }

    const applyRevision = (pattern: RegExp, updater: (value: string) => boolean) => {
      const match = userText.match(pattern);
      if (!match) {
        return false;
      }
      return updater(match[1].trim());
    };

    const revised =
      applyRevision(/(?:change|set|update)\s+slide\s*count\s+(?:to\s+)?(\d+)/i, (value) => {
        const n = Number.parseInt(value, 10);
        if (!Number.isInteger(n) || n < 3 || n > 30) {
          return false;
        }
        brief.slide_count = n;
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_updated', field: 'slide_count', value: n }));
        return true;
      })
      || applyRevision(/(?:set|change|update|make)\s+tone\s+(?:to\s+)?(.+)/i, (value) => {
        const mapped = mapEnum(value, toneMap, 'tone');
        if (!mapped) return false;
        brief.tone = mapped;
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_updated', field: 'tone', value: mapped }));
        return true;
      })
      || applyRevision(/(?:set|change|update|make)\s+technical\s+depth\s+(?:to\s+)?(.+)/i, (value) => {
        const mapped = mapEnum(value, depthMap, 'technical_depth');
        if (!mapped) return false;
        brief.technical_depth = mapped;
        state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_updated', field: 'technical_depth', value: mapped }));
        return true;
      })
      || applyRevision(/(?:set|change|update)\s+deck\s*type\s+(?:to\s+)?(.+)/i, (value) => {
        const mapped = mapEnum(value, deckTypeMap, 'deck_type');
        if (!mapped) return false;
        brief.deck_type = mapped;
        return true;
      })
      || applyRevision(/(?:set|change|update)\s+topic\s+(?:to\s+)?(.+)/i, (value) => {
        brief.topic = value;
        return true;
      })
      || applyRevision(/(?:set|change|update)\s+audience\s+(?:to\s+)?(.+)/i, (value) => {
        brief.audience = value;
        return true;
      })
      || applyRevision(/(?:add|set|change|update)\s+must\s+include\s+(?:to\s+)?(.+)/i, (value) => {
        brief.must_include = toList(value);
        return true;
      })
      || applyRevision(/(?:add|set|change|update)\s+constraints\s+(?:to\s+)?(.+)/i, (value) => {
        brief.constraints = toList(value);
        return true;
      })
      || applyRevision(/(?:add|set|change|update)\s+required\s+messaging\s+(?:to\s+)?(.+)/i, (value) => {
        brief.required_messaging = toList(value);
        return true;
      })
      || applyRevision(/(?:set|change|update)\s+user\s+notes\s+(?:to\s+)?(.+)/i, (value) => {
        brief.user_notes = value;
        return true;
      })
      || applyRevision(/(?:set|change|update)\s+speaker\s+notes\s+(?:to\s+)?(.+)/i, (value) => {
        if (yes(value)) {
          brief.output.speaker_notes = true;
        } else if (no(value)) {
          brief.output.speaker_notes = false;
        } else {
          return false;
        }
        return true;
      });

    if (!revised) {
      return ask('I can revise deck_type, topic, audience, slide_count, tone, technical_depth, must_include, constraints, required_messaging, user_notes, or speaker notes. Please specify one change.');
    }

    return presentReview();
  }

  if (!brief.deck_type) {
    const mapped = mapEnum(userText, deckTypeMap, 'deck_type');
    if (!mapped) {
      return ask('What type of deck are we creating? Choose one: executive briefing, technical deep dive, partner enablement, internal training, architecture review, workshop, roadmap, proposal, or custom.');
    }
    brief.deck_type = mapped;
  } else if (!brief.topic) {
    brief.topic = userText;
  } else if (!brief.audience) {
    brief.audience = userText;
  } else if (!brief.customer_context && !state.skippedFields.includes('customer_context')) {
    if (isSkipInput(userText)) markSkipped('customer_context');
    else brief.customer_context = userText;
  } else if (!brief.industry && !state.skippedFields.includes('industry')) {
    if (isSkipInput(userText)) markSkipped('industry');
    else brief.industry = userText;
  } else if (!brief.use_case && !state.skippedFields.includes('use_case')) {
    if (isSkipInput(userText)) markSkipped('use_case');
    else brief.use_case = userText;
  } else if (typeof brief.slide_count !== 'number') {
    const n = Number.parseInt(userText, 10);
    if (!Number.isInteger(n) || n < 3 || n > 30) {
      return ask('Slide count must be an integer between 3 and 30.');
    }
    brief.slide_count = n;
  } else if (!brief.tone) {
    const mapped = mapEnum(userText, toneMap, 'tone');
    if (!mapped) {
      return ask('What tone should this deck use? Choose one: executive, consultative, technical, technical but executive readable, sales, training, concise, or custom.');
    }
    brief.tone = mapped;
  } else if (!brief.technical_depth) {
    const mapped = mapEnum(userText, depthMap, 'technical_depth');
    if (!mapped) {
      return ask('What technical depth should we target? Choose one: low, medium, high, or mixed.');
    }
    brief.technical_depth = mapped;
  } else if (!brief.must_include && !state.skippedFields.includes('must_include')) {
    if (isSkipInput(userText)) markSkipped('must_include');
    else brief.must_include = toList(userText);
  } else if (!brief.constraints && !state.skippedFields.includes('constraints')) {
    if (isSkipInput(userText)) markSkipped('constraints');
    else brief.constraints = toList(userText);
  } else if (!brief.required_messaging && !state.skippedFields.includes('required_messaging')) {
    if (isSkipInput(userText)) markSkipped('required_messaging');
    else brief.required_messaging = toList(userText);
  } else if (!brief.user_notes && !state.skippedFields.includes('user_notes')) {
    if (isSkipInput(userText)) markSkipped('user_notes');
    else brief.user_notes = userText;
  } else if (typeof brief.output.speaker_notes !== 'boolean') {
    if (yes(userText)) brief.output.speaker_notes = true;
    else if (no(userText)) brief.output.speaker_notes = false;
    else return ask('Should speaker notes be included? Please answer yes or no.');
  }

  const nextQuestion = [
    [!brief.topic, 'What is the presentation topic?'],
    [!brief.audience, 'Who is the audience?'],
    [!brief.customer_context && !state.skippedFields.includes('customer_context'), 'What customer/company context should be included? (or say skip)'],
    [!brief.industry && !state.skippedFields.includes('industry'), 'What industry is this for? (or say skip)'],
    [!brief.use_case && !state.skippedFields.includes('use_case'), 'What is the primary use case? (or say skip)'],
    [typeof brief.slide_count !== 'number', 'How many slides do you want (3–30)?'],
    [!brief.tone, 'What tone should this deck use? Choose one: executive, consultative, technical, technical but executive readable, sales, training, concise, or custom.'],
    [!brief.technical_depth, 'What technical depth should we target? Choose one: low, medium, high, or mixed.'],
    [!brief.must_include && !state.skippedFields.includes('must_include'), 'List must-include sections (comma-separated), or say skip.'],
    [!brief.constraints && !state.skippedFields.includes('constraints'), 'List constraints (comma-separated), or say skip.'],
    [!brief.required_messaging && !state.skippedFields.includes('required_messaging'), 'List required messaging points (comma-separated), or say skip.'],
    [!brief.user_notes && !state.skippedFields.includes('user_notes'), 'Any extra user notes? (or say skip)'],
    [typeof brief.output.speaker_notes !== 'boolean', 'Include speaker notes? (yes/no)'],
  ].find((x) => x[0]);

  if (nextQuestion) {
    return ask(nextQuestion[1] as string);
  }

  return presentReview();
};
