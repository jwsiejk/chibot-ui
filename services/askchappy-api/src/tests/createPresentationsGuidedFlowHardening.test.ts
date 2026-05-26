import { describe, expect, it } from 'vitest';
import {
  appendLocalUserTextMessage,
  createLocalSession,
  generateLocalAssistantMessage,
  getLocalSession,
  getLocalTranscript,
  setLocalSessionMode,
} from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';

describe('create presentations phase 2b hardening', () => {
  it('supports optional skip flow, enum mapping, validations, revisions, and approval gating', async () => {
    resetSessionStore();
    const session = createLocalSession();
    setLocalSessionMode(session.session_id, 'create_presentations', 'user');

    const answer = async (text: string) => {
      appendLocalUserTextMessage(session.session_id, text);
      await generateLocalAssistantMessage(session.session_id);
      return getLocalTranscript(session.session_id).at(-1)?.text ?? '';
    };

    await answer('executive briefing');
    await answer('Q3 modernization');
    await answer('CIO team');
    await answer('skip');
    await answer('n/a');
    await answer('leave blank');

    expect(await answer('2')).toContain('Slide count must be an integer between 3 and 30.');
    expect(await answer('31')).toContain('Slide count must be an integer between 3 and 30.');
    await answer('10');

    const tonePrompt = await answer('weird');
    expect(tonePrompt).toContain('Choose a tone:');
    expect(tonePrompt).toContain('1. Executive');
    expect(tonePrompt).toContain('4. Technical but executive readable');
    await answer('technical but executive readable');

    expect(await answer('hardcore')).toContain('Choose technical depth:');
    await answer('moderate');

    await answer('business drivers, architecture, roadmap');
    await answer('keep concise, one message per slide');
    await answer('risk reduction, measurable outcomes');
    await answer('blank');

    expect(await answer('maybe')).toContain('Include speaker notes?');
    const review = await answer('no');
    expect(review).toContain('Here’s the brief I heard:');
    expect(review).toContain('4. Customer context: Skipped');
    expect(review).toContain('5. Industry: Skipped');
    expect(review).toContain('6. Primary use case: Skipped');
    expect(review).toContain('13. User notes: Skipped');
    expect(review).toContain('1. Deck type: Executive briefing');
    expect(review).toContain('8. Tone: Technical but executive readable');
    expect(review).not.toMatch(/RAG|Glean|DDN|embeddings|citation|internal docs/i);

    expect(await answer('approve with changes')).toContain('Please specify one change');
    expect(await answer('set tone to consultative')).toContain('Here’s the brief I heard:');
    const updatedReview = await answer('change slide count to 8');
    expect(updatedReview).toContain('7. Slide count: 8');

    const approved = await answer('Approve this brief');
    expect(approved).toContain('Great — I approved the brief and generated the outline');
    expect(approved).toContain('Outline review');

    const updated = getLocalSession(session.session_id);
    const cps = updated?.metadata.askchappy.create_presentations_state;
    expect(cps?.deckBrief.deck_type).toBe('customer_executive_briefing');
    expect(cps?.deckBrief.tone).toBe('consultative');
    expect(cps?.deckBrief.technical_depth).toBe('medium');
    expect(cps?.deckBrief.must_include).toEqual(['business drivers', 'architecture', 'roadmap']);
    expect(cps?.deckBrief.constraints).toEqual(['keep concise', 'one message per slide']);
    expect(cps?.deckBrief.required_messaging).toEqual(['risk reduction', 'measurable outcomes']);
    expect(cps?.deckBrief.output.speaker_notes).toBe(false);
    expect(cps?.deckBrief.status).toBe('outline_review');
    expect(cps?.step).toBe('outline_review');
    expect(cps?.outline?.status).toBe('outline_review');
    expect(cps?.skippedFields).toEqual(expect.arrayContaining(['customer_context', 'industry', 'use_case', 'user_notes']));
    expect(cps?.events.some((event) => event.kind === 'validation_error')).toBe(true);
    expect(cps?.events.some((event) => event.kind === 'brief_review_presented')).toBe(true);
    expect(cps?.events.some((event) => event.kind === 'brief_updated' && event.field === 'slide_count')).toBe(true);
  });
});
