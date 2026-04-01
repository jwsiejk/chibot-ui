import { useEffect, useMemo, useRef, useState } from 'react';
import { createPttLifecycleController } from '../audio/pttLifecycle';
import { useAudioFoundation } from '../audio/useAudioFoundation';
import { usePushToTalkRecorder } from '../audio/usePushToTalkRecorder';
import { FloatingChatWindow } from '../chat/FloatingChatWindow';
import { useAskChipController } from '../state/useAskChipController';
import { VisualSessionStage } from './VisualSessionStage';
import { VisualSessionToolbar } from './VisualSessionToolbar';

type VisualSessionViewProps = {
  sessionId: string;
};

export function VisualSessionView({ sessionId }: VisualSessionViewProps) {
  const { state, actions } = useAskChipController();
  const audio = useAudioFoundation(state.currentSessionId);
  const pushToTalk = usePushToTalkRecorder(audio.selectedDeviceId);
  const [chatOpen, setChatOpen] = useState(false);

  const pttRuntimeRef = useRef({ actions, audioSelectedDeviceId: audio.selectedDeviceId, pushToTalkActions: pushToTalk.actions, pushToTalkActive: pushToTalk.active, voiceDisabledReason: state.voiceDisabledReason });

  useEffect(() => {
    pttRuntimeRef.current = {
      actions,
      audioSelectedDeviceId: audio.selectedDeviceId,
      pushToTalkActions: pushToTalk.actions,
      pushToTalkActive: pushToTalk.active,
      voiceDisabledReason: state.voiceDisabledReason,
    };
  }, [actions, audio.selectedDeviceId, pushToTalk.actions, pushToTalk.active, state.voiceDisabledReason]);

  const pttLifecycle = useMemo(() => createPttLifecycleController({
    beginLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.beginCapture(),
    finishLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.finishCapture(),
    cancelLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.cancelCapture(),
    startBackendVoiceTurn: () => pttRuntimeRef.current.actions.startVoiceTurn(
      pttRuntimeRef.current.audioSelectedDeviceId,
      pttRuntimeRef.current.pushToTalkActions.getStartedAt(),
    ),
    cancelBackendVoiceTurn: () => pttRuntimeRef.current.actions.cancelVoiceTurn(),
    submitVoiceTurn: async (recorded) => {
      try {
        await pttRuntimeRef.current.actions.finishVoiceTurn({
          blob: recorded.blob,
          filename: recorded.mimeType.includes('mp4') ? 'voice-turn.mp4' : 'voice-turn.webm',
          deviceId: pttRuntimeRef.current.audioSelectedDeviceId,
          durationMs: recorded.durationMs,
        });
      } finally {
        pttRuntimeRef.current.pushToTalkActions.markComplete();
      }
    },
    isInteractionBlocked: () => Boolean(pttRuntimeRef.current.voiceDisabledReason) || pttRuntimeRef.current.pushToTalkActive,
  }), []);

  useEffect(() => () => {
    pttLifecycle.dispose();
  }, [pttLifecycle]);

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
        stopDisabled={state.topLevelState !== 'listening' && state.topLevelState !== 'transcribing' && state.topLevelState !== 'speaking'}
        onToggleChat={() => setChatOpen((current) => !current)}
        onVoice={() => {
          if (pushToTalk.active) {
            void pttLifecycle.pressRelease();
            return;
          }
          void pttLifecycle.pressStart();
        }}
        onStop={() => {
          void actions.cancelVoiceTurn();
          void pttLifecycle.pressCancel();
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
        voiceDisabledReason={state.voiceDisabledReason}
        liveDraft={state.voiceDraft}
        onSend={actions.sendTurn}
        onPressStart={pttLifecycle.pressStart}
        onPressEnd={pttLifecycle.pressRelease}
        onPressCancel={pttLifecycle.pressCancel}
        onClose={() => setChatOpen(false)}
      />
    </main>
  );
}
