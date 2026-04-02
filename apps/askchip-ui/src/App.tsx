import { ExpertDeskIntakeView } from './demo/ExpertDeskIntakeView';
import { ExpertDeskLandingView } from './demo/ExpertDeskLandingView';
import { ExpertDeskRecommendationView } from './demo/ExpertDeskRecommendationView';
import { ExpertDeskSummaryView } from './demo/ExpertDeskSummaryView';
import { useExpertDeskDemoState } from './demo/useExpertDeskDemoState';
import { resolveAppRoute } from './routing';
import { AskChipShell } from './shell/AskChipShell';
import { VisualSessionView } from './visual-session/VisualSessionView';

function App() {
  const route = resolveAppRoute(window.location.pathname);
  const { intakeDraft, updateIntakeDraft, saveIntakeDraft, readyForRecommendation, hasSessionPersistence } =
    useExpertDeskDemoState();

  if (route.kind === 'visual-session') {
    return <VisualSessionView sessionId={route.sessionId} />;
  }

  if (route.kind === 'demo-home') {
    return <ExpertDeskLandingView />;
  }

  if (route.kind === 'demo-intake') {
    return (
      <ExpertDeskIntakeView
        draft={intakeDraft}
        onChange={updateIntakeDraft}
        onSave={saveIntakeDraft}
        readyForRecommendation={readyForRecommendation}
        hasSessionPersistence={hasSessionPersistence}
      />
    );
  }

  if (route.kind === 'demo-recommendation') {
    return <ExpertDeskRecommendationView draft={intakeDraft} readyForRecommendation={readyForRecommendation} />;
  }

  if (route.kind === 'demo-summary') {
    return <ExpertDeskSummaryView sessionId={route.sessionId} />;
  }

  return <AskChipShell />;
}

export default App;
