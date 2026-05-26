import { CREATE_PRESENTATIONS_DECK_TYPES, CREATE_PRESENTATIONS_TECHNICAL_DEPTH, CREATE_PRESENTATIONS_TONES, type CreatePresentationsModeEvent } from '../../../../shared/contracts/createPresentationsMode';
import type { AskChappySession } from '../sessions/sessionStore';
const nextEvent = (event: Omit<CreatePresentationsModeEvent, 'id' | 'ts'>): CreatePresentationsModeEvent => ({ id: `evt_${crypto.randomUUID()}`, ts: new Date().toISOString(), ...event });
const toList = (input: string) => input.split(',').map((i) => i.trim()).filter(Boolean);
const yes = (t: string) => /^(yes|y|true)/i.test(t.trim());
const no = (t: string) => /^(no|n|false)/i.test(t.trim());
const approve = (t: string) => /(approve|approved|looks good|go ahead|yes,? approve)/i.test(t.trim());

export const handleCreatePresentationsTurn = (session: AskChappySession): string => {
  const state = session.metadata.askchappy.create_presentations_state;
  if (!state) throw new Error('create_presentations_state missing while in create_presentations mode.');
  const userText = [...session.transcript].reverse().find((m) => m.role === 'user')?.text?.trim() ?? '';
  state.events.push(nextEvent({ actor: 'user', step: state.step, kind: 'answer_recorded', text: userText }));
  const brief = state.deckBrief;
  const ask = (text: string) => (state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'question_asked', text })), text);
  if (state.step === 'intro') state.step = 'collecting_brief';
  if (state.step === 'brief_review') {
    const ready = !!(brief.deck_type && brief.topic && brief.audience && brief.slide_count && brief.tone && brief.technical_depth && typeof brief.output.speaker_notes === 'boolean');
    if (!ready) return ask('I cannot approve yet because required fields are missing or invalid. Tell me what to revise.');
    if (approve(userText)) { brief.status = 'brief_approved'; state.step = 'brief_approved'; state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_approved', text: 'Deck Brief approved.' })); return 'Great—your Deck Brief is approved. Phase 2 stops here.'; }
    state.step = 'collecting_brief';
  }
  if (!brief.deck_type) { if (!CREATE_PRESENTATIONS_DECK_TYPES.includes(userText as never)) return ask(`Choose deck type: ${CREATE_PRESENTATIONS_DECK_TYPES.join(', ')}.`); brief.deck_type = userText as never; }
  else if (!brief.topic) { if (!userText) return ask('Please provide the presentation topic (required).'); brief.topic = userText; }
  else if (!brief.audience) { if (!userText) return ask('Please provide the audience (required).'); brief.audience = userText; }
  else if (!brief.customer_context) brief.customer_context = userText || undefined;
  else if (!brief.industry) brief.industry = userText || undefined;
  else if (!brief.use_case) brief.use_case = userText || undefined;
  else if (typeof brief.slide_count !== 'number') { const n = Number.parseInt(userText, 10); if (!Number.isInteger(n) || n < 3 || n > 30) { state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'validation_error', field: 'slide_count', text: 'slide_count must be 3-30' })); return ask('Slide count must be an integer between 3 and 30.'); } brief.slide_count = n; }
  else if (!brief.tone) { if (!CREATE_PRESENTATIONS_TONES.includes(userText as never)) return ask(`Choose tone: ${CREATE_PRESENTATIONS_TONES.join(', ')}.`); brief.tone = userText as never; }
  else if (!brief.technical_depth) { if (!CREATE_PRESENTATIONS_TECHNICAL_DEPTH.includes(userText as never)) return ask(`Choose technical depth: ${CREATE_PRESENTATIONS_TECHNICAL_DEPTH.join(', ')}.`); brief.technical_depth = userText as never; }
  else if (!brief.must_include) brief.must_include = toList(userText);
  else if (!brief.constraints) brief.constraints = toList(userText);
  else if (!brief.required_messaging) brief.required_messaging = toList(userText);
  else if (!brief.user_notes) brief.user_notes = userText;
  else if (typeof brief.output.speaker_notes !== 'boolean') { if (yes(userText)) brief.output.speaker_notes = true; else if (no(userText)) brief.output.speaker_notes = false; else return ask('Should speaker notes be included? Please answer yes or no.'); }
  state.events.push(nextEvent({ actor: 'system', step: state.step, kind: 'brief_updated' }));
  const nextQuestion = [
    [!brief.topic, 'What is the presentation topic?'], [!brief.audience, 'Who is the audience?'], [!brief.customer_context, 'What customer/company context should be included?'], [!brief.industry, 'What industry is this for?'], [!brief.use_case, 'What is the primary use case?'], [typeof brief.slide_count !== 'number', 'How many slides do you want (3–30)?'], [!brief.tone, `Select tone: ${CREATE_PRESENTATIONS_TONES.join(', ')}.`], [!brief.technical_depth, `Select technical depth: ${CREATE_PRESENTATIONS_TECHNICAL_DEPTH.join(', ')}.`], [!brief.must_include, 'List must-include sections (comma-separated).'], [!brief.constraints, 'List constraints (comma-separated).'], [!brief.required_messaging, 'List required messaging points (comma-separated).'], [!brief.user_notes, 'Any extra user notes?'], [typeof brief.output.speaker_notes !== 'boolean', 'Include speaker notes? (yes/no)'],
  ].find((x) => x[0]);
  if (nextQuestion) return ask(nextQuestion[1] as string);
  brief.status = 'brief_review'; state.step = 'brief_review';
  const review = `Deck Brief Review\n- deck_type: ${brief.deck_type}\n- topic: ${brief.topic}\n- audience: ${brief.audience}\n- slide_count: ${brief.slide_count}\n- tone: ${brief.tone}\n- technical_depth: ${brief.technical_depth}\n- speaker_notes: ${brief.output.speaker_notes ? 'yes' : 'no'}\nApprove this brief, or tell me what to revise.`;
  state.events.push(nextEvent({ actor: 'assistant', step: state.step, kind: 'brief_review_presented', text: review }));
  return review;
};
