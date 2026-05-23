import { describe, expect, it, beforeEach } from 'vitest';
import { createLocalSession, appendLocalUserTextMessage, setLocalSessionMode, getLocalSession } from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';
import { generateSessionSummary } from '../summary/sessionSummary';

describe('phase 7 session summary generation', () => {
  beforeEach(() => resetSessionStore());

  it('uses canonical transcript text and metadata final mode', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'Please send follow up notes tomorrow');
    setLocalSessionMode(session.session_id, 'meeting_prep', 'user');

    const loaded = getLocalSession(session.session_id);
    expect(loaded).toBeDefined();

    const summary = generateSessionSummary(loaded!);
    expect(summary.finalMode).toBe('meeting_prep');
    expect(summary.keyDiscussionNotes).toContain('Please send follow up notes tomorrow');
    expect(summary.actionItems[0]).toContain('Action from transcript: Please send follow up notes tomorrow');
  });

  it('renders empty-state messages when transcript is empty', () => {
    const session = createLocalSession();
    const summary = generateSessionSummary(session);

    expect(summary.needsMoreTranscriptContext).toBe(true);
    expect(summary.keyDiscussionNotes[0]).toContain('Not enough transcript context yet');
    expect(summary.followUpDraft).toContain('More transcript context is needed');
  });


  it('does not throw when mode_change event meta is malformed', () => {
    const session = createLocalSession();
    const loaded = getLocalSession(session.session_id);
    expect(loaded).toBeDefined();

    loaded!.events.push({
      id: 'event_malformed',
      ts: new Date().toISOString(),
      session_id: session.session_id,
      event_type: 'mode_change',
      meta: { from_mode: 'open_qa', actor: 'user' },
    });

    expect(() => generateSessionSummary(loaded!)).not.toThrow();
    const summary = generateSessionSummary(loaded!);
    expect(summary.modeHistory.some((line) => line.includes('details unavailable'))).toBe(true);
  });

  it('uses mode change events for mode history and defaults to open_qa history message without changes', () => {
    const first = createLocalSession();
    const firstSummary = generateSessionSummary(first);
    expect(firstSummary.modeHistory).toEqual(['No mode changes recorded. Session stayed in Open Q&A.']);

    const second = createLocalSession();
    setLocalSessionMode(second.session_id, 'learn_ddn', 'user');
    setLocalSessionMode(second.session_id, 'open_qa', 'assistant');
    const secondSummary = generateSessionSummary(getLocalSession(second.session_id)!);

    expect(secondSummary.modeHistory).toHaveLength(2);
    expect(secondSummary.modeHistory[0]).toContain('open_qa → learn_ddn');
    expect(secondSummary.modeHistory[1]).toContain('learn_ddn → open_qa');
  });
});
