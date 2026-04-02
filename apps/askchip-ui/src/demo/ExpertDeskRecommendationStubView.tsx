import { useMemo, useState } from 'react';
import { askChipApiClient } from '../api/client';
import { DEMO_ROUTES } from '../routing';
import {
  buildExpertDeskSessionContextFromDraft,
  saveExpertDeskSessionContext,
} from './expertDeskSessionContext';
import {
  buildExpertDeskRecommendation,
  getContactPreferenceLabel,
  getIssueCategoryLabel,
  getRecommendedPathLabel,
  getUrgencyLabel,
} from './recommendation';
import type { ExpertDeskIntakeDraft } from './types';

type ExpertDeskRecommendationViewProps = {
  draft: ExpertDeskIntakeDraft;
  readyForRecommendation: boolean;
};

export function ExpertDeskRecommendationStubView({ draft, readyForRecommendation }: ExpertDeskRecommendationViewProps) {
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [followUpPreference, setFollowUpPreference] = useState('');
  const recommendation = useMemo(() => buildExpertDeskRecommendation(draft), [draft]);

  const launchLiveSession = async () => {
    setLaunching(true);
    setLaunchError(null);

    try {
      const session = await askChipApiClient.createSession({
        title: `Expert Desk: ${getIssueCategoryLabel(draft.issueCategory)}`,
      });

      const sessionContext = buildExpertDeskSessionContextFromDraft(draft, recommendation);
      saveExpertDeskSessionContext(session.id, sessionContext);

      window.location.href = `/visual-session/${encodeURIComponent(session.id)}`;
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'Unable to launch live session right now.');
      setLaunching(false);
    }
  };

  if (!readyForRecommendation) {
    return (
      <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 md:px-6">
        <div className="mx-auto w-full max-w-4xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Routing</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">Recommendation requires current valid intake</h1>
          <p className="mt-4 text-sm leading-6 text-slate-700">
            Save the intake draft with all required fields currently valid so this route can compute a deterministic recommendation.
          </p>
          <a href={DEMO_ROUTES.intake} className="mt-6 inline-flex text-sm font-medium text-indigo-700 hover:text-indigo-600">
            ← Back to intake
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 md:px-6">
      <div className="mx-auto grid w-full max-w-6xl gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Routing</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">Recommendation & Next Step</h1>
          <p className="mt-3 text-sm leading-6 text-slate-700">{recommendation.issueSummary}</p>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {[
              ['Issue type', getIssueCategoryLabel(draft.issueCategory)],
              ['Environment', draft.environmentPlatform],
              ['Urgency', getUrgencyLabel(draft.urgency)],
              ['Expert persona', recommendation.expertPersona],
              ['Recommended expert type', recommendation.recommendedExpertType],
              ['Recommended path', getRecommendedPathLabel(recommendation.recommendedPath)],
            ].map(([label, value]) => (
              <article key={label} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
                <p className="mt-2 text-sm font-medium text-slate-900">{value}</p>
              </article>
            ))}
          </div>

          <section className="mt-5 rounded-2xl border border-indigo-100 bg-indigo-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-700">Why this was recommended</p>
            <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-indigo-950">
              {recommendation.whyRecommended.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-indigo-800">Recommendation confidence: {recommendation.confidence}.</p>
          </section>

          <section className="mt-5 rounded-2xl border border-slate-200 bg-white p-4">
            <p className="text-sm font-semibold text-slate-900">Follow-up preference request capture (demo-only)</p>
            <p className="mt-2 text-xs leading-5 text-slate-600">
              This field captures a scheduling preference request only in local browser state for the current view. It is
              not sent to a calendar or queue engine.
            </p>
            <textarea
              value={followUpPreference}
              onChange={(event) => setFollowUpPreference(event.target.value)}
              rows={3}
              placeholder="Optional: share preferred day/time windows for a follow-up request."
              className="mt-3 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
            />
          </section>
        </section>

        <aside className="space-y-4">
          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Captured intake context</p>
            <dl className="mt-3 space-y-2 text-sm text-slate-700">
              <div className="flex justify-between gap-2"><dt>Contact preference</dt><dd className="font-medium">{getContactPreferenceLabel(draft.contactPreference)}</dd></div>
              <div className="flex justify-between gap-2"><dt>Submitted at</dt><dd className="font-medium">{draft.submittedAt ?? '—'}</dd></div>
            </dl>
          </section>

          <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5 shadow-sm">
            <p className="text-sm font-semibold text-emerald-900">Launch live expert flow</p>
            <p className="mt-2 text-sm leading-6 text-emerald-900">
              This creates a real AskChip local session, binds recommendation context to that session id in local session storage,
              and opens the visual-session route.
            </p>
            <button
              type="button"
              onClick={() => void launchLiveSession()}
              disabled={launching}
              className="mt-4 inline-flex w-full justify-center rounded-full bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-emerald-300"
            >
              {launching ? 'Launching live session…' : 'Launch live expert session'}
            </button>
            {launchError ? <p className="mt-2 text-xs text-rose-700">{launchError}</p> : null}
          </section>

          <a href={DEMO_ROUTES.intake} className="inline-flex text-sm font-medium text-indigo-700 hover:text-indigo-600">
            ← Back to intake
          </a>
        </aside>
      </div>
    </main>
  );
}
