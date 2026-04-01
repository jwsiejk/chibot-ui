import { useState } from 'react';
import { ExpertDeskIntakeView } from './demo/ExpertDeskIntakeView';
import { ExpertDeskLandingView } from './demo/ExpertDeskLandingView';
import { DEFAULT_EXPERT_DESK_INTAKE_DRAFT, type ExpertDeskIntakeDraft } from './demo/types';
import { resolveAppRoute } from './routing';
import { AskChipShell } from './shell/AskChipShell';
import { VisualSessionView } from './visual-session/VisualSessionView';

function App() {
  const route = resolveAppRoute(window.location.pathname);
  const [intakeDraft, setIntakeDraft] = useState<ExpertDeskIntakeDraft>(DEFAULT_EXPERT_DESK_INTAKE_DRAFT);

  if (route.kind === 'visual-session') {
    return <VisualSessionView sessionId={route.sessionId} />;
  }

  if (route.kind === 'demo-home') {
    return <ExpertDeskLandingView />;
  }

  if (route.kind === 'demo-intake') {
    return <ExpertDeskIntakeView draft={intakeDraft} onChange={setIntakeDraft} />;
  }

  return <AskChipShell />;
}

export default App;
