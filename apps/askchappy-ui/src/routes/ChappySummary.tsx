import React, { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { getLocalSession } from '../../../../services/askchappy-api/src/api/server';
import { generateSessionSummary } from '../../../../services/askchappy-api/src/summary/sessionSummary';
import { buildSummarySections } from '../summary/summarySections';

export const ChappySummary = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const session = useMemo(() => (sessionId ? getLocalSession(sessionId) : undefined), [sessionId]);

  if (!sessionId || !session) {
    return (
      <main>
        <h1>Session not found</h1>
        <p>This local-first summary could not find session {sessionId ?? '(missing id)'}.</p>
      </main>
    );
  }

  const summary = generateSessionSummary(session);
  const sections = buildSummarySections(summary);

  return (
    <main>
      <h1>AskChappy partner recap</h1>
      <p>Session ID: {sessionId}</p>
      <p>Session overview: {summary.sessionOverview}</p>
      <p>Persona: {session.metadata.askchappy.persona_label} ({session.metadata.askchappy.persona_id})</p>
      <p>Active/final mode: {summary.finalMode}</p>

      {sections.map((section) => (
        <section key={section.heading} aria-label={section.heading}>
          <h2>{section.heading}</h2>
          <ul>
            {section.items.map((item, index) => (
              <li key={`${section.heading}-${index}`}>{item}</li>
            ))}
          </ul>
        </section>
      ))}

      <section aria-label="Talk track and follow-up framing">
        <h2>Talk track and follow-up framing</h2>
        <p>{summary.talkTrack}</p>
      </section>

      <section aria-label="Follow-up draft placeholder">
        <h2>Follow-up draft placeholder</h2>
        <p>{summary.followUpDraft}</p>
      </section>
    </main>
  );
};
