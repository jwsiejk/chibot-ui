export const DEMO_ROUTES = {
  home: '/demo',
  intake: '/demo/intake',
  recommendation: '/demo/recommendation',
  summaryBase: '/demo/summary',
} as const;

export function getDemoSummaryRoute(sessionId: string): string {
  return `${DEMO_ROUTES.summaryBase}/${encodeURIComponent(sessionId)}`;
}

export type AppRoute =
  | {
      kind: 'shell';
    }
  | {
      kind: 'visual-session';
      sessionId: string;
    }
  | {
      kind: 'demo-home';
    }
  | {
      kind: 'demo-intake';
    }
  | {
      kind: 'demo-recommendation';
    }
  | {
      kind: 'demo-summary';
      sessionId: string;
    };

function decodePathSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function resolveAppRoute(pathname: string): AppRoute {
  if (pathname === DEMO_ROUTES.home) {
    return { kind: 'demo-home' };
  }

  if (pathname === DEMO_ROUTES.intake) {
    return { kind: 'demo-intake' };
  }

  if (pathname === DEMO_ROUTES.recommendation) {
    return { kind: 'demo-recommendation' };
  }

  const demoSummaryMatch = pathname.match(/^\/demo\/summary\/([^/]+)$/);

  if (demoSummaryMatch) {
    return {
      kind: 'demo-summary',
      sessionId: decodePathSegment(demoSummaryMatch[1]),
    };
  }

  const visualSessionMatch = pathname.match(/^\/visual-session\/([^/]+)$/);

  if (visualSessionMatch) {
    return {
      kind: 'visual-session',
      sessionId: decodePathSegment(visualSessionMatch[1]),
    };
  }

  return { kind: 'shell' };
}
