import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { App } from '../app/App';
import { getLocalSession } from '../../../../services/askchappy-api/src/api/server';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
import { MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
import { VOICE_PROFILE_STATES } from '../../../../shared/contracts/voice';
import { routeMap } from '../routes/routeMap';

describe('route map', () => {
  it('contains all active askchappy routes', () => {
    expect(routeMap).toEqual([
      ROUTES.home,
      ROUTES.chappy,
      ROUTES.chappySession,
      ROUTES.chappySummary,
      ROUTES.dev,
      ROUTES.admin,
      ROUTES.adminVoice,
      ROUTES.adminAvatar,
    ]);
  });

  it('does not include retired routes as active UX routes', () => {
    for (const route of RETIRED_ROUTES) {
      expect(routeMap).not.toContain(route);
    }
  });
});

describe('phase 5 chappy UI', () => {
  it('renders email login gate on /chappy', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText('Local-first MVP login. Enter email to continue.')).toBeInTheDocument();
  });

  it('renders /chappy entry screen after login', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByRole('heading', { name: 'AskChappy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Open Q&A' })).toBeInTheDocument();
  });

  it('shows admin nav links for admin login', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByRole('link', { name: 'Admin' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Voice Studio' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Avatar' })).toBeInTheDocument();
  });

  it('hides admin nav links for standard users', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Voice Studio' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Avatar' })).not.toBeInTheDocument();
  });

  it.each([ROUTES.admin, ROUTES.adminVoice, ROUTES.adminAvatar] as const)(
    'blocks standard user direct access to %s with not authorized',
    (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>,
      );

      expect(screen.getByText('Not authorized')).toBeInTheDocument();
    },
  );

  it.each([
    ['Admin', 'AskChappy local-first admin dashboard'],
    ['Voice Studio', 'Voice Studio shell (admin only)'],
    ['Avatar', 'Avatar setup and review shell (admin only)'],
  ] as const)('allows admin navigation access to %s', (linkName, heading) => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: linkName }));

    expect(screen.getByText(heading)).toBeInTheDocument();
  });


  it('renders local-first admin dashboard shell with voice and avatar links', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Admin' }));

    expect(screen.getByRole('heading', { name: 'AskChappy local-first admin dashboard' })).toBeInTheDocument();
    expect(screen.getByText(/fallback voice active/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Voice Studio' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Avatar review' })).toBeInTheDocument();
  });

  it('renders voice studio shell using shared voice lifecycle states with inactive controls', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Voice Studio' }));

    expect(screen.getByRole('heading', { name: 'Voice Studio shell (admin only)' })).toBeInTheDocument();
    for (const lifecycleState of VOICE_PROFILE_STATES) {
      expect(screen.getByText(lifecycleState)).toBeInTheDocument();
    }
    expect(screen.getByText('No published Chappy voice profile.')).toBeInTheDocument();
    expect(screen.getByText('Fallback voice path is active.')).toBeInTheDocument();
    expect(screen.getByText('Real voice cloning is not implemented in Phase 8.')).toBeInTheDocument();

    const futureControlsSection = screen.getByRole('region', { name: 'future voice workflow controls' });
    const disabledControls = within(futureControlsSection).getAllByRole('button', {
      name: /Record or upload voice samples|Create draft profile|Test generated speech|Approve profile|Publish global voice|Disable or revert to fallback/,
    });
    expect(disabledControls).toHaveLength(6);
    disabledControls.forEach((control) => expect(control).toBeDisabled());
  });

  it('does not show Voice Studio controls in normal /chappy/session/:sessionId user sessions', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    expect(screen.queryByRole('heading', { name: 'Voice Studio shell (admin only)' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Record or upload voice samples' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create draft profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Test generated speech' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Publish global voice' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Disable or revert to fallback' })).not.toBeInTheDocument();
  });

  it('keeps admin voice/avatar shells free of committed private voice/avatar asset imports', () => {
    const voiceShellSource = readFileSync(resolve(process.cwd(), 'apps/askchappy-ui/src/admin/voice/VoiceStudioPage.tsx'), 'utf8');
    const avatarShellSource = readFileSync(resolve(process.cwd(), 'apps/askchappy-ui/src/admin/avatar/AvatarAdminPage.tsx'), 'utf8');
    const combinedSource = `${voiceShellSource}\n${avatarShellSource}`.toLowerCase();

    expect(combinedSource).not.toMatch(/\.(wav|mp3|m4a|ogg|flac|webm|bin|pt|ckpt|onnx|npy|npz|pkl|emb|embedding|jpg|jpeg|png|webp|gif|glb|gltf|fbx|obj)['"]/);
    expect(combinedSource).not.toContain('/assets/voice');
    expect(combinedSource).not.toContain('/assets/avatar');
  });

  it('renders avatar shell placeholder and future state placeholders', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Avatar' }));

    expect(screen.getByRole('heading', { name: 'Avatar setup and review shell (admin only)' })).toBeInTheDocument();
    expect(screen.getByText('Placeholder avatar is active.')).toBeInTheDocument();
    expect(screen.getByText('Real avatar implementation is not implemented in Phase 8.')).toBeInTheDocument();
    expect(screen.getByText('Static branded Chappy image')).toBeInTheDocument();
    expect(screen.getByText('State-aware avatar')).toBeInTheDocument();
    expect(screen.getByText('Speaking/viseme-capable avatar')).toBeInTheDocument();
  });

  it('start open q&a creates local session and navigates to session shell', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    expect(screen.getByRole('heading', { name: 'AskChappy session' })).toBeInTheDocument();
    expect(screen.getByLabelText('chappy stage')).toBeInTheDocument();
    expect(screen.getByText('Session state indicator: ready')).toBeInTheDocument();
    expect(screen.getByLabelText('transcript panel')).toBeInTheDocument();
    expect(screen.getByLabelText('typed input form')).toBeInTheDocument();
  });

  it('typed input appends user canonical transcript message with typed source and text', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'hello chappy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.getByText('user:')).toBeInTheDocument();
    expect(screen.getByText(/hello chappy/)).toBeInTheDocument();
  });

  it('empty typed input does not append transcript message', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.queryByText('user:')).not.toBeInTheDocument();
  });

  it('renders right rail in open_qa mode initially', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    const rightRail = screen.getByRole('complementary', { name: 'session right rail' });
    expect(within(rightRail).getByRole('heading', { name: 'Current mode' })).toBeInTheDocument();
    expect(within(rightRail).getByRole('button', { name: 'Open Q&A' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(rightRail).getByText('Ask Chappy anything about DDN positioning, use cases, or partner scenarios.')).toBeInTheDocument();
  });

  it('switches guided modes and keeps session id/transcript intact', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    const sessionText = screen.getByText(/Local production working session ID:/).textContent ?? '';
    const sessionId = sessionText.split(': ').at(-1) ?? '';
    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'keep this transcript' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    fireEvent.click(screen.getByRole('button', { name: 'Learn DDN' }));
    expect(screen.getByText('Build foundational DDN understanding from basics to field usage.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Meeting Prep' }));
    expect(screen.getByText('Prepare meeting objectives, agenda, discovery questions, and talk tracks.')).toBeInTheDocument();
    expect(screen.getByText(/keep this transcript/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open Q&A' }));
    expect(screen.getByText('Ask Chappy anything about DDN positioning, use cases, or partner scenarios.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'still typing after mode switch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.getByText(/still typing after mode switch/)).toBeInTheDocument();

    const session = getLocalSession(sessionId);
    expect(session?.session_id).toBe(sessionId);
    expect(session?.metadata.askchappy.session_mode).toBe('open_qa');
    expect(session?.metadata.askchappy.persona_id).toBe('ddn_chappy_vptm');
    expect(session?.metadata.askchappy.persona_label).toBe('Chappy');

    const modeChanges = session?.events.filter((event) => event.event_type === 'mode_change') ?? [];
    expect(modeChanges).toHaveLength(3);
    expect(modeChanges.map((event) => ({
      event_type: event.event_type,
      from_mode: event.meta.from_mode,
      to_mode: event.meta.to_mode,
      actor: event.meta.actor,
    }))).toEqual([
      { event_type: 'mode_change', from_mode: 'open_qa', to_mode: 'learn_ddn', actor: 'user' },
      { event_type: 'mode_change', from_mode: 'learn_ddn', to_mode: 'meeting_prep', actor: 'user' },
      { event_type: 'mode_change', from_mode: 'meeting_prep', to_mode: 'open_qa', actor: 'user' },
    ]);

    expect(session?.transcript.every((message) => message.role === 'user')).toBe(true);
    expect(session?.transcript.every((message) => message.text !== 'mode_change')).toBe(true);
    expect(screen.queryByText('mode_change')).not.toBeInTheDocument();
  });
  it('keeps voice studio controls absent in normal /chappy/session route', () => {
    render(
      <MemoryRouter initialEntries={['/chappy/session/session_123']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Voice Studio')).not.toBeInTheDocument();
  });

  const retiredRouteCases = ['/demo', '/demo/intake', '/demo/recommendation', '/visual-session/session_123', '/demo/summary/session_123'] as const;

  it.each(retiredRouteCases)('resolves retired route %s as non-active UX', (path) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Route not found' })).toBeInTheDocument();
  });
});
