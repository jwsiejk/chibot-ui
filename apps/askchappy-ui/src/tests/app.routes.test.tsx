import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { App } from '../app/App';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
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
  const activeRouteCases = [
    { path: ROUTES.home, heading: 'AskChappy entry placeholder' },
    { path: ROUTES.chappy, heading: 'AskChappy entry placeholder' },
    { path: '/chappy/session/session_123', heading: 'AskChappy Zoom-like session placeholder' },
    { path: '/chappy/summary/session_123', heading: 'AskChappy recap placeholder' },
    { path: ROUTES.dev, heading: 'Diagnostics placeholder (hidden from main flow)' },
    { path: ROUTES.admin, heading: 'Admin placeholder' },
    { path: ROUTES.adminVoice, heading: 'Admin Voice Studio placeholder' },
    { path: ROUTES.adminAvatar, heading: 'Admin Avatar placeholder' },
  ] as const;

  it.each(activeRouteCases)('renders active route $path', ({ path, heading }) => {
    render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
  });

  const retiredRouteCases = [
    '/demo',
    '/demo/intake',
    '/demo/recommendation',
    '/visual-session/session_123',
    '/demo/summary/session_123',
  ] as const;

  it.each(retiredRouteCases)('resolves retired route %s as non-active UX', (path) => {
    render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'Route not found' })).toBeInTheDocument();
  });
});
