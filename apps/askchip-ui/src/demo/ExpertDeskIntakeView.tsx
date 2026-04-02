import type { ChangeEvent, FormEvent } from 'react';
import { DEMO_ROUTES } from '../routing';
import { ExpertDeskFlowProgress } from './ExpertDeskFlowProgress';
import type { ExpertDeskIntakeDraft, ExpertPersonaId, IntakeUrgency } from './types';

type ExpertDeskIntakeViewProps = {
  draft: ExpertDeskIntakeDraft;
  onChange: (next: ExpertDeskIntakeDraft) => void;
  onSave: () => void;
  readyForRecommendation: boolean;
  hasSessionPersistence: boolean;
};

const urgencyLabels: Record<IntakeUrgency, string> = {
  'same-day': 'Same day (service impact now)',
  'this-week': 'This week (priority issue)',
  planned: 'Planned (no immediate outage)',
};

const expertPersonaLabels: Record<ExpertPersonaId, string> = {
  'ai-vmware-engineer': 'AI VMware Engineer',
  'ai-aws-engineer': 'AI AWS Engineer',
  'ai-backup-recovery-engineer': 'AI Backup / Recovery Engineer',
  'ai-data-center-engineer': 'AI Data Center Engineer',
  'general-infrastructure-expert': 'General Infrastructure Expert',
};

export function ExpertDeskIntakeView({
  draft,
  onChange,
  onSave,
  readyForRecommendation,
  hasSessionPersistence,
}: ExpertDeskIntakeViewProps) {
  const updateField = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = event.target;
    onChange({ ...draft, [name]: value });
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSave();
  };

  const submitDisabled =
    !draft.issueCategory ||
    !draft.environmentPlatform ||
    !draft.urgency ||
    !draft.preferredExpertPersonaId ||
    !draft.contactPreference ||
    draft.issueDescription.trim().length < 20;

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 md:px-6">
      <div className="mx-auto grid w-full max-w-6xl gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
          <ExpertDeskFlowProgress currentStep="intake" />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Intake</p>
              <h1 className="mt-1 text-2xl font-semibold text-slate-950">Issue Intake & Expert Routing</h1>
            </div>
            <a href={DEMO_ROUTES.home} className="text-sm font-medium text-indigo-700 hover:text-indigo-600">
              ← Demo landing
            </a>
          </div>

          <p className="mt-3 text-sm leading-6 text-slate-600">
            This form stores intake details in frontend sessionStorage for demo purposes only. No CRM, calendar, or
            backend intake submission is performed.
          </p>

          <form className="mt-6 space-y-5" onSubmit={handleSubmit}>
            <div className="grid gap-5 md:grid-cols-2">
              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Issue category
                <select
                  required
                  name="issueCategory"
                  value={draft.issueCategory}
                  onChange={updateField}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                >
                  <option value="">Select a category</option>
                  <option value="production-outage">Production outage</option>
                  <option value="integration-failure">Integration failure</option>
                  <option value="security-review">Security review</option>
                  <option value="migration-planning">Migration planning</option>
                </select>
              </label>

              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Environment / platform
                <input
                  required
                  name="environmentPlatform"
                  value={draft.environmentPlatform}
                  onChange={updateField}
                  placeholder="e.g., Azure + AKS + Postgres"
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                />
              </label>

              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Urgency
                <select
                  required
                  name="urgency"
                  value={draft.urgency}
                  onChange={updateField}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                >
                  <option value="">Select urgency</option>
                  <option value="same-day">Same day</option>
                  <option value="this-week">This week</option>
                  <option value="planned">Planned</option>
                </select>
              </label>

              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Preferred expert persona
                <select
                  required
                  name="preferredExpertPersonaId"
                  value={draft.preferredExpertPersonaId}
                  onChange={updateField}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                >
                  <option value="">Select expert persona</option>
                  <option value="ai-vmware-engineer">AI VMware Engineer</option>
                  <option value="ai-aws-engineer">AI AWS Engineer</option>
                  <option value="ai-backup-recovery-engineer">AI Backup / Recovery Engineer</option>
                  <option value="ai-data-center-engineer">AI Data Center Engineer</option>
                  <option value="general-infrastructure-expert">General Infrastructure Expert</option>
                </select>
              </label>

              <label className="grid gap-2 text-sm font-medium text-slate-700 md:col-span-2">
                Contact preference
                <select
                  required
                  name="contactPreference"
                  value={draft.contactPreference}
                  onChange={updateField}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                >
                  <option value="">Select contact preference</option>
                  <option value="live-session-now">Live Expert Desk session now</option>
                  <option value="scheduled-session">Schedule a guided session</option>
                  <option value="async-brief">Async written brief first</option>
                </select>
              </label>
            </div>

            <label className="grid gap-2 text-sm font-medium text-slate-700">
              Issue description
              <textarea
                required
                minLength={20}
                name="issueDescription"
                value={draft.issueDescription}
                onChange={updateField}
                rows={5}
                placeholder="Describe symptoms, impact, and what changed recently."
                className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </label>

            <div className="grid gap-5 md:grid-cols-2">
              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Architecture notes (optional)
                <textarea
                  name="architectureNotes"
                  value={draft.architectureNotes}
                  onChange={updateField}
                  rows={4}
                  placeholder="Relevant services, dependencies, and data flows."
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                />
              </label>

              <label className="grid gap-2 text-sm font-medium text-slate-700">
                Error text (optional)
                <textarea
                  name="errorText"
                  value={draft.errorText}
                  onChange={updateField}
                  rows={4}
                  placeholder="Paste stack trace or error snippet."
                  className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                />
              </label>
            </div>

            <button
              type="submit"
              disabled={submitDisabled}
              className="inline-flex rounded-full bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              Save intake draft
            </button>
          </form>
        </section>

        <aside className="space-y-4">
          <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Local state snapshot</p>
            <dl className="mt-3 space-y-2 text-sm text-slate-700">
              <div className="flex justify-between gap-2"><dt>Category</dt><dd className="font-medium">{draft.issueCategory || '—'}</dd></div>
              <div className="flex justify-between gap-2"><dt>Urgency</dt><dd className="font-medium">{draft.urgency ? urgencyLabels[draft.urgency] : '—'}</dd></div>
              <div className="flex justify-between gap-2"><dt>Expert</dt><dd className="font-medium">{draft.preferredExpertPersonaId ? expertPersonaLabels[draft.preferredExpertPersonaId] : '—'}</dd></div>
              <div className="flex justify-between gap-2"><dt>Contact</dt><dd className="font-medium">{draft.contactPreference || '—'}</dd></div>
            </dl>
            <p className="mt-4 text-xs text-slate-500">Saved timestamp: {draft.submittedAt ?? 'Not submitted yet'}</p>
          </section>

          <section className="rounded-3xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900 shadow-sm">
            <p className="font-semibold">Demo integrity note</p>
            <p className="mt-2 leading-6">
              Intake persistence is frontend-only in this browser session via sessionStorage. No backend write, CRM
              sync, or calendar booking is implied.
            </p>
            {hasSessionPersistence ? (
              <p className="mt-2 text-xs text-amber-800">Persistence scope: this tab session only; it resets when the browser session ends.</p>
            ) : null}
          </section>

          <section
            className={`rounded-3xl border p-5 text-sm shadow-sm ${
              readyForRecommendation
                ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
                : 'border-slate-200 bg-white text-slate-700'
            }`}
          >
            <p className="font-semibold">
              {readyForRecommendation ? 'Intake saved and ready for next step' : 'Next step'}
            </p>
            <p className="mt-2 leading-6">
              {readyForRecommendation
                ? 'This intake draft is ready to hand off into Expert Desk recommendation/routing work.'
                : 'Complete required fields and save the intake draft to mark it ready for recommendation/routing.'}
            </p>
            <a
              href={readyForRecommendation ? DEMO_ROUTES.recommendation : undefined}
              className={`mt-3 inline-flex rounded-full px-4 py-2 text-xs font-semibold ${
                readyForRecommendation
                  ? 'bg-emerald-700 text-white hover:bg-emerald-600'
                  : 'pointer-events-none border border-slate-300 bg-white text-slate-500'
              }`}
              aria-disabled={!readyForRecommendation}
            >
              Continue to recommendation handoff
            </a>
            {!readyForRecommendation ? (
              <p className="mt-2 text-xs text-slate-500">Recommendation logic unlocks after this intake draft is saved.</p>
            ) : null}
          </section>
        </aside>
      </div>
    </main>
  );
}
