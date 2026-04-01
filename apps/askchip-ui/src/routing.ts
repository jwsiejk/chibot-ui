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
    };

function decodePathSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function resolveAppRoute(pathname: string): AppRoute {
  if (pathname === '/demo') {
    return { kind: 'demo-home' };
  }

  if (pathname === '/demo/intake') {
    return { kind: 'demo-intake' };
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
