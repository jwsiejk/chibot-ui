import { ExpertDeskIntakeView } from './demo/ExpertDeskIntakeView';
import { ExpertDeskLandingView } from './demo/ExpertDeskLandingView';
import { ExpertDeskRecommendationStubView } from './demo/ExpertDeskRecommendationStubView';
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
    return <ExpertDeskRecommendationStubView />;
  }

  return <AskChipShell />;
}

export default App;
