export type AppRoute =
  | {
      kind: 'shell';
    }
  | {
      kind: 'visual-session';
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
  const visualSessionMatch = pathname.match(/^\/visual-session\/([^/]+)$/);

  if (visualSessionMatch) {
    return {
      kind: 'visual-session',
      sessionId: decodePathSegment(visualSessionMatch[1]),
    };
  }

  return { kind: 'shell' };
}
