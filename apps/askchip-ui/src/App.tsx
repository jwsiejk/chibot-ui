import { AskChipShell } from './shell/AskChipShell';
import { VisualSessionView } from './visual-session/VisualSessionView';
import { resolveAppRoute } from './routing';

function App() {
  const route = resolveAppRoute(window.location.pathname);

  if (route.kind === 'visual-session') {
    return <VisualSessionView sessionId={route.sessionId} />;
  }

  return <AskChipShell />;
}

export default App;
