import { DEMO_ROUTES } from '../routing';

export function ExpertDeskRecommendationStubView() {
  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 md:px-6">
      <div className="mx-auto w-full max-w-4xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Demo</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-950">Recommendation Step (Stub)</h1>
        <p className="mt-4 text-sm leading-6 text-slate-700">
          This frontstage route is intentionally a Phase 7 placeholder. Recommendation and routing logic are not yet
          implemented in this patch.
        </p>
        <a href={DEMO_ROUTES.intake} className="mt-6 inline-flex text-sm font-medium text-indigo-700 hover:text-indigo-600">
          ← Back to intake
        </a>
      </div>
    </main>
  );
}
