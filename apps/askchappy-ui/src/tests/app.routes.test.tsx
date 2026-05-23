import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App } from '../app/App';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
import { MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
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
    ['Admin', 'Admin placeholder'],
    ['Voice Studio', 'Admin Voice Studio placeholder'],
    ['Avatar', 'Admin Avatar placeholder'],
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

  it('renders right rail mode scaffold without switching behavior', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start Open Q&A' }));

    expect(screen.getByText('Open Q&A')).toBeInTheDocument();
    expect(screen.getByText('Learn DDN')).toBeInTheDocument();
    expect(screen.getByText('Meeting Prep')).toBeInTheDocument();
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
