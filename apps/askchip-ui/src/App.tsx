import { AskChipShell } from './shell/AskChipShell';
import { VisualSessionView } from './visual-session/VisualSessionView';

function parseVisualSessionPath(pathname: string): string | null {
  const match = pathname.match(/^\/visual-session\/([^/]+)$/);
  if (!match) {
    return null;
  }

  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

function App() {
  const sessionId = parseVisualSessionPath(window.location.pathname);

  if (sessionId) {
    return <VisualSessionView sessionId={sessionId} />;
  }

  return <AskChipShell />;
}

export default App;
