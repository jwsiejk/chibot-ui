import { useEffect, useMemo, useState } from 'react';
import { ChatPanel } from '../chat/ChatPanel';
import { runtimeConfig } from '../config/runtime';
import { getExpertDeskSessionContext } from '../demo/expertDeskSessionContext';
import { useSessionInteractionRuntime } from '../interaction/useSessionInteractionRuntime';
import { VisualSessionStage } from './VisualSessionStage';
import { VisualSessionToolbar } from './VisualSessionToolbar';

type VisualSessionViewProps = {
  sessionId: string;
};

export function VisualSessionView({ sessionId }: VisualSessionViewProps) {
  const runtime = useSessionInteractionRuntime({ initialSessionId: sessionId });
  const { state, actions, pushToTalk, pttLifecycle, sendTypedTurn, stopInteraction, stopDisabled } = runtime;
  const [chatOpen, setChatOpen] = useState(false);
  const assistantName = runtimeConfig.assistantDisplayName;
  const frontstageContext = useMemo(() => getExpertDeskSessionContext(sessionId), [sessionId]);

  useEffect(() => {
    const title = state.currentSession?.title?.trim();
    document.title = title ? `${title} · AskChip Visual Session` : 'AskChip Visual Session';
  }, [state.currentSession?.title]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (chatOpen && event.key === 'Escape') {
        event.preventDefault();
        setChatOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [chatOpen]);

  useEffect(() => {
    if (!chatOpen) {
      return;
    }
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [chatOpen]);

  useEffect(() => {
    if (state.bootstrapping) {
      return;
    }
    if (!state.sessions.some((session) => session.id === sessionId)) {
      return;
    }
    if (state.currentSessionId === sessionId) {
      return;
    }
    void actions.selectSession(sessionId).catch(() => {
      // selection failures surface through controller appError state
    });
  }, [actions, sessionId, state.bootstrapping, state.currentSessionId, state.sessions]);

  if (state.bootstrapping) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#040812_0%,#050d1e_45%,#020617_100%)] px-6 text-slate-100">
        <div className="w-full max-w-lg rounded-3xl border border-slate-700/80 bg-slate-900/70 px-6 py-6 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-100/80">Preparing visual session</p>
          <h1 className="mt-2 text-lg font-semibold text-white">Loading session context…</h1>
          <p className="mt-2 text-sm text-slate-300">We’re syncing transcript, controls, and assistant stage state.</p>
        </div>
      </main>
    );
  }

  const sessionNotAvailable = !state.currentSession || state.currentSession.id !== sessionId;
  if (sessionNotAvailable) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#040812_0%,#050d1e_45%,#020617_100%)] px-6 text-slate-100">
        <div className="w-full max-w-xl rounded-3xl border border-rose-400/30 bg-rose-500/10 p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-rose-100/80">Session unavailable</p>
          <h1 className="mt-2 text-xl font-semibold text-white">We couldn’t open this visual session.</h1>
          <p className="mt-2 text-sm leading-6 text-rose-50/90">
            {state.appError ?? `Session "${sessionId}" no longer exists or could not be loaded.`}
          </p>
          <a
            href="/"
            className="mt-4 inline-flex rounded-full border border-slate-200/30 bg-slate-900/60 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-100 transition hover:border-slate-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70"
          >
            Return to AskChip shell
          </a>
        </div>
      </main>
    );
  }

  const hasMessages = state.messages.length > 0;

  return (
    <main className="relative min-h-screen overflow-hidden bg-[linear-gradient(180deg,#040812_0%,#050d1e_45%,#020617_100%)] text-slate-100">
      <header className="mx-auto w-full max-w-[1460px] px-6 pb-4 pt-5">
        <div className="rounded-3xl border border-slate-800/90 bg-slate-900/45 px-5 py-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-200/80">AskChip visual session</p>
              <h1 className="mt-1 text-xl font-semibold text-white">{state.currentSession?.title ?? `Session ${sessionId}`}</h1>
            </div>
            <div className="rounded-full border border-slate-700 bg-slate-900/70 px-3 py-1 text-xs uppercase tracking-[0.16em] text-slate-300">
              {assistantName} · {state.topLevelState ?? 'ready'}
            </div>
          </div>
          <p className="mt-2 text-sm text-slate-300">Live specialist engagement surface with shared transcript + voice runtime.</p>
        </div>

        {frontstageContext ? (
          <div className="mt-3 rounded-3xl border border-cyan-300/25 bg-slate-900/70 px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-100/80">Expert desk context</p>
            <div className="mt-2 grid gap-2 text-xs text-slate-200 md:grid-cols-2 xl:grid-cols-3">
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Request</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.requestLabel}</p></div>
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Issue category</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.issueCategoryLabel}</p></div>
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Environment</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.environment}</p></div>
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Urgency</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.urgencyLabel}</p></div>
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Expert persona</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.expertPersona}</p></div>
              <div className="rounded-xl border border-slate-700/80 bg-slate-950/55 px-3 py-2"><span className="text-slate-400">Recommended path</span><p className="mt-1 text-sm font-medium text-white">{frontstageContext.recommendedPathLabel}</p></div>
            </div>
          </div>
        ) : null}
      </header>

      <section className="mx-auto grid w-full max-w-[1460px] gap-5 px-6 pb-28 xl:grid-cols-[300px_minmax(0,1fr)_320px]">
        {frontstageContext ? (
          <aside className="rounded-[1.6rem] border border-cyan-300/20 bg-slate-900/45 p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/80">Expert Assist</p>
            <h2 className="mt-2 text-base font-semibold text-white">Session briefing rail</h2>
            <div className="mt-4 space-y-3 text-sm">
              <article className="rounded-2xl border border-slate-700/80 bg-slate-950/60 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Recommended next step</p>
                <p className="mt-2 text-slate-100">{frontstageContext.recommendedNextStep}</p>
              </article>
              <article className="rounded-2xl border border-slate-700/80 bg-slate-950/60 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Likely topic / root-cause hint</p>
                <p className="mt-2 text-slate-100">{frontstageContext.likelyTopicHint}</p>
              </article>
              <article className="rounded-2xl border border-slate-700/80 bg-slate-950/60 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-slate-400">Retrieved case context (saved intake/recommendation)</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-200">
                  {frontstageContext.retrievedCaseContext.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-slate-400">{frontstageContext.sourceNote}</p>
              </article>
              <article className="rounded-2xl border border-amber-300/30 bg-amber-300/10 p-3">
                <p className="text-xs uppercase tracking-[0.16em] text-amber-100">Escalation note</p>
                <p className="mt-2 text-amber-50">{frontstageContext.escalationNote}</p>
              </article>
            </div>
          </aside>
        ) : null}

        <VisualSessionStage state={state.topLevelState} assistantName={assistantName} />

        <aside className="hidden rounded-[1.6rem] border border-slate-800 bg-slate-900/45 p-5 xl:block">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/80">Session controls</p>
          <h2 className="mt-2 text-base font-semibold text-white">Chat and interaction</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Open chat from the toolbar to review transcript, send typed messages, and hold Mic for voice input.
          </p>
          <div className="mt-4 space-y-2 text-xs text-slate-300">
            <div className="rounded-2xl border border-slate-700/80 bg-slate-900/65 px-3 py-2">
              {hasMessages ? `${state.messages.length} messages in this session` : 'No messages yet.'}
            </div>
            <p className="text-slate-400">Press <span className="rounded border border-slate-600 px-1 py-0.5 text-[10px]">Esc</span> to close chat.</p>
          </div>
        </aside>
      </section>

      {state.appError && (
        <div className="pointer-events-none fixed inset-x-0 top-0 z-20 mx-auto mt-3 flex w-full max-w-[760px] justify-center px-6">
          <div className="w-full rounded-2xl border border-rose-400/35 bg-rose-500/15 px-4 py-2 text-sm text-rose-50 shadow-lg">
            {state.appError}
          </div>
        </div>
      )}

      <div
        className={[
          'fixed inset-0 z-20 bg-slate-950/35 transition-opacity duration-200 lg:hidden',
          chatOpen ? 'opacity-100' : 'pointer-events-none opacity-0',
        ].join(' ')}
        onClick={() => setChatOpen(false)}
        aria-hidden={!chatOpen}
      />

      <VisualSessionToolbar
        chatOpen={chatOpen}
        voiceDisabled={Boolean(state.voiceDisabledReason)}
        voiceActive={pushToTalk.active}
        stopDisabled={stopDisabled}
        voiceDisabledReason={state.voiceDisabledReason ?? pushToTalk.status.error}
        onToggleChat={() => setChatOpen((current) => !current)}
        onVoice={() => {
          if (pushToTalk.active) {
            void pttLifecycle.pressRelease();
            return;
          }
          void pttLifecycle.pressStart();
        }}
        onStop={() => {
          void stopInteraction('toolbar_stop');
        }}
      />

      <ChatPanel
        mode="drawer"
        open={chatOpen}
        sessionTitle={state.currentSession?.title ?? null}
        messages={state.messages}
        empty={!hasMessages}
        disabledReason={state.sendingDisabledReason}
        pending={state.pendingTurn}
        voiceDisabled={Boolean(state.voiceDisabledReason)}
        voiceDisabledReason={state.voiceDisabledReason ?? pushToTalk.status.error}
        liveDraft={state.voiceDraft}
        onSend={sendTypedTurn}
        onPressStart={pttLifecycle.pressStart}
        onPressEnd={pttLifecycle.pressRelease}
        onPressCancel={pttLifecycle.pressCancel}
        onClose={() => setChatOpen(false)}
      />
    </main>
  );
}
