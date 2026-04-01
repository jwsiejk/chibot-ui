import { useEffect, useState } from 'react';
import { FloatingChatWindow } from '../chat/FloatingChatWindow';
import { useSessionInteractionRuntime } from '../interaction/useSessionInteractionRuntime';
import { VisualSessionStage } from './VisualSessionStage';
import { VisualSessionToolbar } from './VisualSessionToolbar';

type VisualSessionViewProps = {
  sessionId: string;
};

export function VisualSessionView({ sessionId }: VisualSessionViewProps) {
  const runtime = useSessionInteractionRuntime();
  const { state, actions, pushToTalk, pttLifecycle, sendTypedTurn, stopInteraction, stopDisabled } = runtime;
  const [chatOpen, setChatOpen] = useState(false);

  useEffect(() => {
    if (state.currentSessionId === sessionId) {
      return;
    }
    void actions.selectSession(sessionId);
  }, [actions, sessionId, state.currentSessionId]);

  const hasMessages = state.messages.length > 0;

  return (
    <main className="relative min-h-screen overflow-hidden bg-[linear-gradient(180deg,#040812_0%,#050d1e_45%,#020617_100%)] text-slate-100">
      <header className="mx-auto flex w-full max-w-[1400px] items-center justify-between gap-4 px-6 py-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-cyan-200/80">AskChip visual session</p>
          <h1 className="text-lg font-semibold text-white">{state.currentSession?.title ?? `Session ${sessionId}`}</h1>
        </div>
        <div className="rounded-full border border-slate-700 bg-slate-900/70 px-3 py-1 text-xs uppercase tracking-[0.16em] text-slate-300">
          Assistant: Chip · State {state.topLevelState ?? 'ready'}
        </div>
      </header>

      <section className="mx-auto grid w-full max-w-[1400px] gap-5 px-6 pb-28 lg:grid-cols-[minmax(0,1fr)_340px]">
        <VisualSessionStage state={state.topLevelState} />

        <aside className="hidden rounded-[1.6rem] border border-slate-800 bg-slate-900/45 p-4 lg:block">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/80">Chat panel</p>
          <h2 className="mt-2 text-base font-semibold text-white">Right drawer reserved for live chat</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Keep this surface for in-meeting transcript and controls. For this patch, chat opens as a floating panel from the toolbar.
          </p>
          <div className="mt-4 rounded-2xl border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-400">
            {hasMessages ? `${state.messages.length} transcript messages synced` : 'No transcript messages yet.'}
          </div>
        </aside>
      </section>

      <VisualSessionToolbar
        chatOpen={chatOpen}
        voiceDisabled={Boolean(state.voiceDisabledReason)}
        voiceActive={pushToTalk.active}
        stopDisabled={stopDisabled}
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

      <FloatingChatWindow
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
