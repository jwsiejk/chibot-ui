export const DEMO_ROUTES = {
  home: '/demo',
  intake: '/demo/intake',
  recommendation: '/demo/recommendation',
} as const;

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

  const visualSessionMatch = pathname.match(/^\/visual-session\/([^/]+)$/);

  if (visualSessionMatch) {
    return {
      kind: 'visual-session',
      sessionId: decodePathSegment(visualSessionMatch[1]),
    };
  }

  return { kind: 'shell' };
}
