import { useEffect, useMemo, useState } from 'react';
import { ApiError, askChipApiClient } from '../api/client';
import { DEMO_ROUTES } from '../routing';
import type { SessionRecord, TranscriptMessage } from '../types/contract';
import { ExpertDeskFlowProgress } from './ExpertDeskFlowProgress';
import {
  getExpertDeskLocalHandoffRequest,
  getExpertDeskSessionContext,
  saveExpertDeskLocalHandoffRequest,
  type ExpertDeskLocalHandoffRequestType,
  type ExpertDeskSessionContext,
} from './expertDeskSessionContext';

type ExpertDeskSummaryViewProps = {
  sessionId: string;
};

type SummaryDataState = {
  loading: boolean;
  error: string | null;
  session: SessionRecord | null;
  messages: TranscriptMessage[];
  context: ExpertDeskSessionContext | null;
};

function formatDateTime(value: string | null): string {
  if (!value) {
    return 'Not available';
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function buildFallbackContext(session: SessionRecord | null): ExpertDeskSessionContext {
  const sessionLabel = session?.title?.trim() || 'Expert Desk request';

  return {
    requestLabel: sessionLabel,
    issueCategoryLabel: 'Not captured in Expert Desk intake context',
    environment: 'Not captured in Expert Desk intake context',
    urgencyLabel: 'Not captured in Expert Desk intake context',
    expertPersona: 'General specialist context not available for this session id',
    recommendedPathLabel: 'Review transcript and determine next routing step',
    recommendedNextStep: 'Review this transcript and decide whether to continue AI triage, schedule follow-up, or escalate.',
    likelyTopicHint: 'Use transcript evidence to confirm issue scope, impact, and unresolved blockers.',
    escalationNote: 'Escalation can be requested from this summary, but requests are local-only in this browser session.',
    retrievedCaseContext: ['No session-linked intake context was found for this session id.'],
    sourceNote: 'Fallback summary context is derived from session metadata only.',
  };
}

function buildDerivedActions(messages: TranscriptMessage[]): string[] {
  const userCount = messages.filter((message) => message.role === 'user' && message.text.trim()).length;
  const assistantCount = messages.filter((message) => message.role === 'assistant' && message.text.trim()).length;
  const voiceCount = messages.filter((message) => message.source === 'voice_input').length;

  const actions: string[] = [];

  if (userCount > 0 || assistantCount > 0) {
    actions.push(`Derived from transcript: ${userCount} user message(s) and ${assistantCount} assistant message(s) were captured.`);
  }

  if (voiceCount > 0) {
    actions.push(`Derived from transcript: ${voiceCount} turn(s) were captured through voice input.`);
  }

  const latestAssistant = [...messages]
    .reverse()
    .find((message) => message.role === 'assistant' && message.text.trim().length > 0);

  if (latestAssistant) {
    const preview = latestAssistant.text.trim().slice(0, 180);
    actions.push(`Derived from transcript: latest assistant guidance preview — "${preview}${latestAssistant.text.trim().length > 180 ? '…' : ''}"`);
  }

  if (actions.length === 0) {
    actions.push('No transcript exchanges were captured for this session, so no action summary can be derived yet.');
  }

  return actions;
}

export function ExpertDeskSummaryView({ sessionId }: ExpertDeskSummaryViewProps) {
  const [dataState, setDataState] = useState<SummaryDataState>({
    loading: true,
    error: null,
    session: null,
    messages: [],
    context: null,
  });
  const [requestType, setRequestType] = useState<ExpertDeskLocalHandoffRequestType>('follow-up-session');
  const [requestNote, setRequestNote] = useState('');
  const [requestSavedNotice, setRequestSavedNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    setDataState({
      loading: true,
      error: null,
      session: null,
      messages: [],
      context: getExpertDeskSessionContext(sessionId),
    });

    void askChipApiClient
      .getTranscript(sessionId)
      .then((transcript) => {
        if (!active) {
          return;
        }

        setDataState({
          loading: false,
          error: null,
          session: transcript.session,
          messages: transcript.messages
            .filter((message) => message.session_id === sessionId)
            .sort((left, right) => left.created_at.localeCompare(right.created_at)),
          context: getExpertDeskSessionContext(sessionId),
        });
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }

        const message = error instanceof ApiError
          ? error.detail
          : error instanceof Error
            ? error.message
            : 'Unable to load this session summary.';

        setDataState({
          loading: false,
          error: message,
          session: null,
          messages: [],
          context: getExpertDeskSessionContext(sessionId),
        });
      });

    return () => {
      active = false;
    };
  }, [sessionId]);

  const localRequest = useMemo(() => getExpertDeskLocalHandoffRequest(sessionId), [requestSavedNotice, sessionId]);

  useEffect(() => {
    if (!localRequest) {
      return;
    }
    setRequestType(localRequest.type);
    setRequestNote(localRequest.note);
  }, [localRequest]);

  const summaryContext = useMemo(
    () => dataState.context ?? buildFallbackContext(dataState.session),
    [dataState.context, dataState.session],
  );

  const actionsTaken = useMemo(() => buildDerivedActions(dataState.messages), [dataState.messages]);

  if (dataState.loading) {
    return (
      <main className="min-h-screen bg-slate-100 px-4 py-10 text-slate-900 md:px-6">
        <div className="mx-auto w-full max-w-5xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
          <ExpertDeskFlowProgress currentStep="summary" sessionId={sessionId} />
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Summary</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">Loading post-session handoff…</h1>
          <p className="mt-3 text-sm text-slate-700">Fetching actual session transcript and available Expert Desk context for session {sessionId}.</p>
        </div>
      </main>
    );
  }

  if (dataState.error && !dataState.session) {
    return (
      <main className="min-h-screen bg-slate-100 px-4 py-10 text-slate-900 md:px-6">
        <div className="mx-auto w-full max-w-4xl rounded-3xl border border-rose-200 bg-white p-8 shadow-sm">
          <ExpertDeskFlowProgress currentStep="summary" sessionId={sessionId} />
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-rose-700">Summary unavailable</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">This session could not be loaded.</h1>
          <p className="mt-3 text-sm leading-6 text-slate-700">
            {dataState.error}. The session may have been deleted or the session id may be invalid.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a href={DEMO_ROUTES.home} className="inline-flex rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-800 hover:bg-slate-100">
              Back to Expert Desk landing
            </a>
            <a href="/" className="inline-flex rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800">
              Open AskChip shell
            </a>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-100 px-4 py-8 text-slate-900 md:px-6">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
          <ExpertDeskFlowProgress currentStep="summary" sessionId={sessionId} />
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-700">Expert Desk Handoff Summary</p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-950">Session closeout and next-step plan</h1>
          <p className="mt-3 text-sm leading-6 text-slate-700">
            This handoff combines real session transcript data for <span className="font-medium text-slate-900">{sessionId}</span>
            {' '}with any session-linked Expert Desk context found in this browser session.
          </p>
          <div className="mt-4 grid gap-3 text-sm md:grid-cols-3">
            <article className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Session title</p>
              <p className="mt-1 font-medium text-slate-900">{dataState.session?.title ?? 'Not available'}</p>
            </article>
            <article className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Started</p>
              <p className="mt-1 font-medium text-slate-900">{formatDateTime(dataState.session?.created_at ?? null)}</p>
            </article>
            <article className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Last activity</p>
              <p className="mt-1 font-medium text-slate-900">{formatDateTime(dataState.session?.last_message_at ?? null)}</p>
            </article>
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(300px,0.8fr)]">
          <div className="space-y-6">
            <article className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Issue summary</h2>
              <p className="mt-3 text-sm leading-6 text-slate-700">{summaryContext.requestLabel}</p>
              <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs uppercase tracking-[0.16em] text-slate-500">Issue category</dt><dd className="mt-1 font-medium text-slate-900">{summaryContext.issueCategoryLabel}</dd></div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs uppercase tracking-[0.16em] text-slate-500">Environment</dt><dd className="mt-1 font-medium text-slate-900">{summaryContext.environment}</dd></div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs uppercase tracking-[0.16em] text-slate-500">Urgency</dt><dd className="mt-1 font-medium text-slate-900">{summaryContext.urgencyLabel}</dd></div>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3"><dt className="text-xs uppercase tracking-[0.16em] text-slate-500">Expert persona</dt><dd className="mt-1 font-medium text-slate-900">{summaryContext.expertPersona}</dd></div>
              </dl>
            </article>

            <article className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Key context captured</h2>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-slate-700">
                {summaryContext.retrievedCaseContext.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-slate-500">{summaryContext.sourceNote}</p>
            </article>

            <article className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Actions taken (derived from transcript/session data)</h2>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-slate-700">
                {actionsTaken.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </article>

            <article className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-950">Recommended next steps</h2>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-slate-700">
                <li>{summaryContext.recommendedPathLabel}</li>
                <li>{summaryContext.recommendedNextStep}</li>
                <li>{summaryContext.escalationNote}</li>
              </ul>
            </article>
          </div>

          <aside className="space-y-6">
            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">Transcript follow-up</h2>
              <p className="mt-2 text-sm text-slate-700">
                Re-open the live session transcript for detailed review or to continue the conversation in the same session.
              </p>
              <a href={`/visual-session/${encodeURIComponent(sessionId)}`} className="mt-4 inline-flex w-full justify-center rounded-full bg-indigo-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-600">
                Open live session transcript
              </a>
              <p className="mt-2 text-xs text-slate-500">This returns to the same live session id; it does not represent backend session termination.</p>
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">Request next-step handoff (local capture only)</h2>
              <p className="mt-2 text-xs leading-5 text-slate-600">
                This request is saved only in frontend session storage for this browser session. It is not sent to CRM, calendar, or ticketing systems.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={() => setRequestType('follow-up-session')}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold ${requestType === 'follow-up-session' ? 'bg-indigo-700 text-white' : 'border border-slate-300 text-slate-700'}`}
                >
                  Request follow-up session
                </button>
                <button
                  type="button"
                  onClick={() => setRequestType('human-escalation')}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold ${requestType === 'human-escalation' ? 'bg-indigo-700 text-white' : 'border border-slate-300 text-slate-700'}`}
                >
                  Request human escalation
                </button>
              </div>
              <textarea
                rows={3}
                value={requestNote}
                onChange={(event) => setRequestNote(event.target.value)}
                placeholder="Optional note about timing, constraints, or preferred follow-up."
                className="mt-3 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
              <button
                type="button"
                onClick={() => {
                  saveExpertDeskLocalHandoffRequest(
                    sessionId,
                    {
                      type: requestType,
                      note: requestNote.trim(),
                      updatedAt: new Date().toISOString(),
                    },
                    summaryContext,
                  );
                  setRequestSavedNotice('Saved locally in this browser session.');
                }}
                className="mt-3 inline-flex w-full justify-center rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-100"
              >
                Save local handoff request
              </button>
              {requestSavedNotice ? <p className="mt-2 text-xs text-emerald-700">{requestSavedNotice}</p> : null}
              {localRequest ? (
                <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50 p-3 text-xs text-indigo-900">
                  <p className="font-semibold uppercase tracking-[0.14em]">Latest local handoff request</p>
                  <p className="mt-1">
                    Type: <span className="font-medium">{localRequest.type === 'human-escalation' ? 'Human escalation' : 'Follow-up session'}</span>
                  </p>
                  <p className="mt-1">Saved: {formatDateTime(localRequest.updatedAt)}</p>
                  <p className="mt-1">Note: {localRequest.note.trim() ? localRequest.note : 'No note provided.'}</p>
                  <p className="mt-1 text-indigo-700">
                    Frontend-local only: this request is not sent to backend, CRM, calendar, queue, or ticketing services.
                  </p>
                </div>
              ) : null}
            </section>
          </aside>
        </section>
      </div>
    </main>
  );
}
