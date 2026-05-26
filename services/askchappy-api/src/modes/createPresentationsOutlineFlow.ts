import type {
  CreatePresentationsDeckBrief,
  CreatePresentationsOutlineSlide,
  CreatePresentationsOutlineState,
} from '../../../../shared/contracts/createPresentationsMode';

const clean = (value: string) => value.replace(/\s+/g, ' ').trim();
const withFallback = (value: string | undefined, fallback: string) => clean(value ?? '') || fallback;

const splitList = (values: string[] | undefined) => (values ?? []).map((value) => clean(value)).filter(Boolean);

export const isOutlineGenerationRequest = (input: string): boolean => /^(generate outline|create outline|build outline|go ahead|continue|next)$/i.test(input.trim());
export const isOutlineApproval = (input: string): boolean => /^(approve|approved|looks good|go ahead|yes,? approve it|approve outline)$/i.test(input.trim());
export const isOutlineRegenerateRequest = (input: string): boolean => /^(regenerate outline|start outline over)$/i.test(input.trim());

export const validateOutlineForApproval = (outline: CreatePresentationsOutlineState, brief: CreatePresentationsDeckBrief): string[] => {
  const errors: string[] = [];
  if (outline.status !== 'outline_review') errors.push('Outline must be in outline_review status before approval.');
  if (outline.slides.length !== brief.slide_count) errors.push(`Outline must contain exactly ${brief.slide_count} slides.`);

  outline.slides.forEach((slide, idx) => {
    const slideLabel = `Slide ${idx + 1}`;
    if (!slide.slide_number) errors.push(`${slideLabel} is missing slide_number.`);
    if (!clean(slide.title)) errors.push(`${slideLabel} title is required.`);
    if (!clean(slide.objective)) errors.push(`${slideLabel} objective is required.`);
    if (slide.key_points.length < 2 || slide.key_points.length > 5) errors.push(`${slideLabel} must contain 2–5 key points.`);
    if (brief.output.speaker_notes && !clean(slide.speaker_notes_prompt ?? '')) errors.push(`${slideLabel} speaker_notes_prompt is required.`);
  });

  return errors;
};

const baseKeyPoints = (brief: CreatePresentationsDeckBrief) => {
  const points = [
    ...splitList(brief.required_messaging),
    ...splitList(brief.constraints),
  ];
  return points.slice(0, 3);
};

export const generateOutlineFromBrief = (brief: CreatePresentationsDeckBrief, nowIso: string): CreatePresentationsOutlineState => {
  const slideCount = brief.slide_count;
  const mustInclude = splitList(brief.must_include);
  const sharedPoints = baseKeyPoints(brief);
  const slides: CreatePresentationsOutlineSlide[] = [];

  const makeSlide = (slide_number: number, title: string, objective: string, key_points: string[]) => ({
    slide_number,
    title: clean(title),
    objective: clean(objective),
    key_points: key_points.map((k) => clean(k)).filter(Boolean).slice(0, 5),
    ...(brief.output.speaker_notes
      ? { speaker_notes_prompt: `Speaker notes for slide ${slide_number}: explain ${clean(title).toLowerCase()} for ${brief.audience}.` }
      : {}),
  });

  slides.push(makeSlide(1,
    `${withFallback(brief.topic, 'Presentation')} — Executive Framing`,
    `Frame ${withFallback(brief.topic, 'the topic')} for ${withFallback(brief.audience, 'the audience')}.`,
    [
      `Purpose: ${withFallback(brief.topic, 'presentation overview')}`,
      `Audience focus: ${withFallback(brief.audience, 'stakeholders')}`,
      `Tone and depth: ${brief.tone} / ${brief.technical_depth}`,
    ]));

  if (slideCount >= 2) {
    slides.push(makeSlide(2,
      'Business Context and Current Challenge',
      'Establish the current situation and why action is needed now.',
      [
        withFallback(brief.customer_context, `Context for ${withFallback(brief.audience, 'stakeholders')}`),
        withFallback(brief.use_case, 'Primary business and technical use case'),
        withFallback(brief.industry, 'Industry constraints and expectations'),
      ]));
  }

  const remaining = slideCount - slides.length;
  const tailSlots = Math.min(2, Math.max(1, remaining - 1));
  const middleTarget = slideCount - slides.length - tailSlots;

  for (let i = 0; i < middleTarget; i += 1) {
    const include = mustInclude[i] ?? `Core Workstream ${i + 1}`;
    slides.push(makeSlide(
      slides.length + 1,
      `${include} Plan`,
      `Detail the ${include.toLowerCase()} approach for this deck.`,
      [
        `${include}: what decisions are needed`,
        `${include}: implementation approach`,
        ...sharedPoints,
      ].slice(0, 5),
    ));
  }

  if (slides.length < slideCount - 1) {
    slides.push(makeSlide(
      slides.length + 1,
      'Implementation Roadmap and Risk Management',
      'Show phased execution, dependencies, and mitigation approach.',
      [
        'Phased rollout with owners and timeline',
        'Operational and delivery risks with mitigations',
        'Success criteria and checkpoints',
      ],
    ));
  }

  while (slides.length < slideCount - 1) {
    slides.push(makeSlide(
      slides.length + 1,
      `Value Realization and Alignment ${slides.length + 1}`,
      'Connect expected outcomes to business priorities.',
      [
        'Expected business impact',
        'Operational simplicity and resilience considerations',
        'Alignment to required messaging and constraints',
      ],
    ));
  }

  slides.push(makeSlide(
    slideCount,
    'Next Steps and Decision Points',
    'Close with clear decisions, owners, and immediate actions.',
    [
      'Decision points requiring stakeholder alignment',
      'Immediate next actions and owners',
      withFallback(brief.user_notes, 'Open discussion and confirmation of priorities'),
    ],
  ));

  return { status: 'outline_review', slides, created_at: nowIso, updated_at: nowIso };
};

export const renderOutlineReview = (outline: CreatePresentationsOutlineState): string => [
  'Deck Outline Review',
  ...outline.slides.flatMap((slide) => {
    const lines = [
      `${slide.slide_number}. ${slide.title}`,
      `   Objective: ${slide.objective}`,
      '   Key points:',
      ...slide.key_points.map((point) => `   - ${point}`),
    ];
    if (slide.speaker_notes_prompt) lines.push(`   Speaker notes prompt: ${slide.speaker_notes_prompt}`);
    return lines;
  }),
  'Approve this outline, or tell me what to revise.',
].join('\n');
