import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App } from '../app/App';
import { createLocalSession, appendLocalUserTextMessage, setLocalSessionMode, getLocalSession } from '../../../../services/askchappy-api/src/api/server';
import { resetSessionStore } from '../../../../services/askchappy-api/src/sessions/sessionStore';

describe('phase 7 summary route', () => {
  beforeEach(() => resetSessionStore());

  it('loads summary for existing local session and distinguishes events from transcript', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'We should review architecture and send follow-up notes.');
    setLocalSessionMode(session.session_id, 'technical_deep_dive', 'user');

    const initialTranscriptCount = getLocalSession(session.session_id)?.transcript.length;
    const initialEventCount = getLocalSession(session.session_id)?.events.length;

    render(
      <MemoryRouter initialEntries={[`/chappy/summary/${session.session_id}`]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'AskChappy partner recap' })).toBeInTheDocument();
    expect(screen.getByText(/Active\/final mode: technical_deep_dive/)).toBeInTheDocument();
    expect(screen.getByText(/ddn_chappy_vptm/)).toBeInTheDocument();
    expect(screen.getByText(/Persona: Chappy \(ddn_chappy_vptm\)/)).toBeInTheDocument();
    expect(screen.getByText(/^We should review architecture and send follow-up notes\.$/)).toBeInTheDocument();

    const modeHistory = screen.getByRole('region', { name: 'Mode history' });
    expect(within(modeHistory).getByText(/open_qa → technical_deep_dive/)).toBeInTheDocument();
    expect(screen.queryByText(/^mode_change$/)).not.toBeInTheDocument();

    expect(screen.getByRole('region', { name: 'Action items' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Talk track and follow-up framing' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Follow-up draft placeholder' })).toBeInTheDocument();

    const after = getLocalSession(session.session_id);
    expect(after?.transcript.length).toBe(initialTranscriptCount);
    expect(after?.events.length).toBe(initialEventCount);
  });

  it('shows not found state for missing session id', () => {
    render(
      <MemoryRouter initialEntries={['/chappy/summary/session_missing']}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'Session not found' })).toBeInTheDocument();
    expect(screen.getByText(/local-first summary could not find session/)).toBeInTheDocument();
  });

  it('shows empty transcript and follow-up context-needed messaging', () => {
    const session = createLocalSession();

    render(
      <MemoryRouter initialEntries={[`/chappy/summary/${session.session_id}`]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText(/Not enough transcript context yet\. Add user conversation in session to build recap notes\./)).toBeInTheDocument();
    expect(screen.getByText(/More transcript context is needed/)).toBeInTheDocument();
  });


  it('does not render support-ticket or triage framing in summary UI copy', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'Please send a follow-up note to the partner.');

    render(
      <MemoryRouter initialEntries={[`/chappy/summary/${session.session_id}`]}>
        <App />
      </MemoryRouter>,
    );

    const pageText = screen.getByRole('main').textContent?.toLowerCase() ?? '';
    expect(pageText).not.toContain('support ticket');
    expect(pageText).not.toContain('case handoff');
    expect(pageText).not.toContain('triage');
    expect(pageText).not.toContain('helpdesk');
  });

  it('retired summary route remains inactive', () => {
    render(
      <MemoryRouter initialEntries={['/demo/summary/session_123']}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'Route not found' })).toBeInTheDocument();
  });
});
