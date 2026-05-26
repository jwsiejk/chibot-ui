import { describe, expect, it } from 'vitest';
import { appendLocalUserTextMessage, createLocalSession, generateLocalAssistantMessage, getLocalSession, getLocalTranscript, setLocalSessionMode } from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';

describe('create presentations phase 3 outline flow', () => {
  it('gates outline generation, supports review/revision/regenerate/approval, and stops before PPTX', async () => {
    resetSessionStore();
    const session = createLocalSession();
    setLocalSessionMode(session.session_id, 'create_presentations', 'user');
    const say = async (text: string) => { appendLocalUserTextMessage(session.session_id, text); await generateLocalAssistantMessage(session.session_id); return getLocalTranscript(session.session_id).at(-1)?.text ?? ''; };

    const start = await say('generate presentation');
    expect(start).toContain('Choose one of the options below:');
    expect(start).toContain('1. Executive briefing');
    const answers = ['executive briefing','Topic A','Audience A','skip','skip','skip','6','4','4','architecture, roadmap','keep concise','risk reduction','skip','2'];
    for (const a of answers) await say(a);
    const readyMsg = await say('Approve this brief');
    expect(readyMsg).toContain('Deck Outline Review');
    expect(readyMsg).toContain('Great — I approved the brief and generated the outline');

    const reviewState = getLocalSession(session.session_id)?.metadata.askchappy.create_presentations_state;
    expect(reviewState?.step).toBe('outline_review');
    expect(reviewState?.outline.status).toBe('outline_review');

    expect(await say('change slide 3 title to Implementation Roadmap')).toContain('Implementation Roadmap');
    expect(await say('change slide 4 objective to Explain operational impact')).toContain('Explain operational impact');
    expect(await say('change slide 5 key points to performance, simplicity, resilience')).toContain('- performance');
    expect(await say('add key point to slide 6: validate recovery process')).toContain('validate recovery process');
    expect(await say('remove key point from slide 6: validate recovery process')).not.toContain('validate recovery process');
    expect(await say('change slide 12 title to x')).toContain('out of range');
    expect(await say('please improve')).toContain('Please clarify the outline revision');

    const reviewTurn = await say('what sources did you use?');
    expect(reviewTurn).not.toMatch(/RAG|Glean|DDN|retrieval|citation/i);

    expect(await say('regenerate outline')).toContain('Deck Outline Review');
    const approved = await say('approve outline');
    expect(approved).toContain('Phase 3 is complete');
    expect(approved).toContain('No PPTX generation happens in this phase');

    const cps = getLocalSession(session.session_id)?.metadata.askchappy.create_presentations_state;
    expect(cps?.outline.status).toBe('outline_approved');
    expect(cps?.step).toBe('outline_approved');
    expect(cps?.deckBrief.status).toBe('outline_approved');
    expect(cps?.outline.slides).toHaveLength(6);
    expect(cps?.outline.slides.every((slide) => slide.key_points.length >= 2 && slide.key_points.length <= 5)).toBe(true);
    expect(cps?.outline.slides.every((slide) => !!slide.speaker_notes_prompt)).toBe(true);
    expect(cps?.events.some((event) => event.kind === 'outline_generated')).toBe(true);
    expect(cps?.events.some((event) => event.kind === 'outline_review_presented')).toBe(true);
    expect(cps?.events.some((event) => event.kind === 'outline_updated')).toBe(true);
  });
});
