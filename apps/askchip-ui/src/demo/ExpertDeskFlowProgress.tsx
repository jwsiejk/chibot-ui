import { DEMO_ROUTES, getDemoSummaryRoute } from '../routing';

type ExpertDeskFlowStepKey = 'landing' | 'intake' | 'recommendation' | 'live-session' | 'summary';

type ExpertDeskFlowProgressProps = {
  currentStep: ExpertDeskFlowStepKey;
  sessionId?: string;
};

type StepConfig = {
  key: ExpertDeskFlowStepKey;
  label: string;
  href?: string;
};

const STEP_ORDER: StepConfig[] = [
  { key: 'landing', label: 'Landing', href: DEMO_ROUTES.home },
  { key: 'intake', label: 'Intake', href: DEMO_ROUTES.intake },
  { key: 'recommendation', label: 'Recommendation', href: DEMO_ROUTES.recommendation },
  { key: 'live-session', label: 'Live session' },
  { key: 'summary', label: 'Summary' },
];

const STEP_INDEX: Record<ExpertDeskFlowStepKey, number> = {
  landing: 0,
  intake: 1,
  recommendation: 2,
  'live-session': 3,
  summary: 4,
};

export function ExpertDeskFlowProgress({ currentStep, sessionId }: ExpertDeskFlowProgressProps) {
  const activeIndex = STEP_INDEX[currentStep];

  return (
    <nav aria-label="Expert Desk demo progress" className="rounded-2xl border border-slate-200/80 bg-white/80 px-4 py-3">
      <ol className="flex flex-wrap items-center gap-2">
        {STEP_ORDER.map((step, index) => {
          const isActive = index === activeIndex;
          const isComplete = index < activeIndex;
          const isLocked = index > activeIndex;
          const href =
            step.key === 'summary' && sessionId
              ? getDemoSummaryRoute(sessionId)
              : step.key === 'live-session' && sessionId
                ? `/visual-session/${encodeURIComponent(sessionId)}`
                : step.href;

          const body = (
            <span
              className={[
                'inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em]',
                isActive
                  ? 'border-indigo-400 bg-indigo-50 text-indigo-800'
                  : isComplete
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                    : 'border-slate-200 bg-slate-50 text-slate-500',
              ].join(' ')}
            >
              {index + 1}. {step.label}
            </span>
          );

          return (
            <li key={step.key} className="flex items-center gap-2">
              {href && !isLocked ? (
                <a href={href} className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/80">
                  {body}
                </a>
              ) : (
                body
              )}
              {index < STEP_ORDER.length - 1 ? <span className="text-xs text-slate-400">→</span> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
