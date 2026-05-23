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
  it('redirects / to /chappy', () => {
    render(<MemoryRouter initialEntries={[ROUTES.home]}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'AskChappy entry placeholder' })).toBeInTheDocument();
  });

  it('renders session placeholder route', () => {
    render(<MemoryRouter initialEntries={['/chappy/session/session_123']}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'AskChappy Zoom-like session placeholder' })).toBeInTheDocument();
  });

  it('renders recap placeholder route', () => {
    render(<MemoryRouter initialEntries={['/chappy/summary/session_123']}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'AskChappy recap placeholder' })).toBeInTheDocument();
  });

  it('retired routes resolve as non-active UX', () => {
    render(<MemoryRouter initialEntries={['/demo']}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'Route not found' })).toBeInTheDocument();
  });
});
