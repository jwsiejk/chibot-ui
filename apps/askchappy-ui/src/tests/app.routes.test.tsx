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

describe('app render scaffold', () => {
  it('renders email login gate on /chappy', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText('Local/demo-only MVP login. Enter email to continue.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument();
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

  const standardUserAdminAccessCases = [ROUTES.admin, ROUTES.adminVoice, ROUTES.adminAvatar] as const;

  it.each(standardUserAdminAccessCases)('blocks standard user direct access to %s', (path) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Not authorized' })).toBeInTheDocument();
  });

  const adminAccessCases = [
    { path: ROUTES.admin, heading: 'Admin placeholder' },
    { path: ROUTES.adminVoice, heading: 'Admin Voice Studio placeholder' },
    { path: ROUTES.adminAvatar, heading: 'Admin Avatar placeholder' },
  ] as const;

  it.each(adminAccessCases)('allows admin access to $heading', ({ heading }) => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: heading.includes('Voice') ? 'Voice Studio' : heading.includes('Avatar') ? 'Avatar' : 'Admin' }));

    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
  });

  const activeRouteCases = [
    { path: ROUTES.home, heading: 'AskChappy entry placeholder' },
    { path: '/chappy/session/session_123', heading: 'AskChappy Zoom-like session placeholder' },
    { path: '/chappy/summary/session_123', heading: 'AskChappy recap placeholder' },
    { path: ROUTES.dev, heading: 'Diagnostics placeholder (hidden from main flow)' },
  ] as const;

  it.each(activeRouteCases)('renders standard access route $path', ({ path, heading }) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
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
