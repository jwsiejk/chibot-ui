import { useEffect, useMemo, useRef, useState } from 'react';
import { MicSetupPanel } from '../audio/MicSetupPanel';
import { useAssistantSpeechPlayback } from '../audio/useAssistantSpeechPlayback';
import { useAudioFoundation } from '../audio/useAudioFoundation';
import { createPttLifecycleController } from '../audio/pttLifecycle';
import { usePushToTalkRecorder } from '../audio/usePushToTalkRecorder';
import { askChipApiClient } from '../api/client';
import { FloatingChatWindow } from '../chat/FloatingChatWindow';
import { ChipStagePane } from '../chip-stage/ChipStagePane';
import { DiagnosticsDrawer } from '../diagnostics/DiagnosticsDrawer';
import { SessionList } from '../sessions/SessionList';
import { useAskChipController } from '../state/useAskChipController';
import { FloatingTranscriptWindow } from '../transcript/FloatingTranscriptWindow';
import type { TranscriptMessage } from '../types/contract';
import { UtilityRail } from '../utility/UtilityRail';
import { useShellPanels } from './useShellPanels';

function findActiveModelName(messages: ReturnType<typeof useAskChipController>['state']['messages']): string | null {
  const assistant = [...messages].reverse().find((message) => message.role === 'assistant');
  const model = assistant?.metadata.model;
  return typeof model === 'string' ? model : null;
}

function toChronologicalTranscript(messages: TranscriptMessage[], sessionId: string): TranscriptMessage[] {
  return messages
    .filter((message) => message.session_id === sessionId)
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
}

export function AskChipShell() {
  const { state, actions } = useAskChipController();
  const audio = useAudioFoundation(state.currentSessionId);
  const pushToTalk = usePushToTalkRecorder(audio.selectedDeviceId);
  const { showDiagnostics, showUtilityRail, toggleDiagnostics, toggleUtilityRail } = useShellPanels();
  const modelName = findActiveModelName(state.messages) ?? state.config?.ollama_model ?? null;
  const speech = useAssistantSpeechPlayback(state.currentSessionId, state.messages);
  const [chatOpen, setChatOpen] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySessionId, setHistorySessionId] = useState<string | null>(null);
  const [historySessionTitle, setHistorySessionTitle] = useState<string | null>(null);
  const [historyMessages, setHistoryMessages] = useState<TranscriptMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const interruptSpeaking = async (reason: string) => {
    if (state.topLevelState === 'speaking' || speech.pendingMessageId || speech.activeMessageId) {
      await speech.stop(reason);
    }
  };

  const sendTypedTurn = async (text: string) => {
    await interruptSpeaking('typed_submit');
    await actions.sendTurn(text);
  };

  const pttRuntimeRef = useRef({ actions, audioSelectedDeviceId: audio.selectedDeviceId, pushToTalkActions: pushToTalk.actions, pushToTalkActive: pushToTalk.active, voiceDisabledReason: state.voiceDisabledReason, interruptSpeaking });

  useEffect(() => {
    pttRuntimeRef.current = {
      actions,
      audioSelectedDeviceId: audio.selectedDeviceId,
      pushToTalkActions: pushToTalk.actions,
      pushToTalkActive: pushToTalk.active,
      voiceDisabledReason: state.voiceDisabledReason,
      interruptSpeaking,
    };
  }, [actions, audio.selectedDeviceId, interruptSpeaking, pushToTalk.actions, pushToTalk.active, state.voiceDisabledReason]);

  const pttLifecycle = useMemo(() => createPttLifecycleController({
    beginLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.beginCapture(),
    finishLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.finishCapture(),
    cancelLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.cancelCapture(),
    startBackendVoiceTurn: async () => {
      await pttRuntimeRef.current.interruptSpeaking('ptt_start');
      return pttRuntimeRef.current.actions.startVoiceTurn(
        pttRuntimeRef.current.audioSelectedDeviceId,
        pttRuntimeRef.current.pushToTalkActions.getStartedAt(),
      );
    },
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
    if (!historySessionId) {
      return;
    }

    const sessionStillExists = state.sessions.some((session) => session.id === historySessionId);
    if (!sessionStillExists) {
      setHistoryOpen(false);
      setHistorySessionId(null);
      setHistorySessionTitle(null);
      setHistoryMessages([]);
      setHistoryLoading(false);
      setHistoryError(null);
      return;
    }

    const session = state.sessions.find((item) => item.id === historySessionId) ?? null;
    setHistorySessionTitle(session?.title ?? null);
  }, [historySessionId, state.sessions]);

  const openVisualSession = async (sessionId: string) => {
    if (state.currentSessionId !== sessionId) {
      await actions.selectSession(sessionId);
    }

    const visualSessionUrl = `${window.location.origin}/visual-session/${encodeURIComponent(sessionId)}`;
    window.open(visualSessionUrl, '_blank', 'noopener,noreferrer');
  };

  const openTranscriptHistory = async (sessionId: string) => {
    const session = state.sessions.find((item) => item.id === sessionId) ?? null;
    setHistoryOpen(true);
    setHistorySessionId(sessionId);
    setHistorySessionTitle(session?.title ?? null);
    setHistoryLoading(true);
    setHistoryError(null);

    try {
      const transcript = await askChipApiClient.getTranscript(sessionId);
      setHistoryMessages(toChronologicalTranscript(transcript.messages, sessionId));
    } catch (error) {
      setHistoryMessages([]);
      setHistoryError(error instanceof Error ? error.message : 'Unable to load transcript history.');
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-surface px-4 py-6 text-slate-100 md:px-6 md:py-8">
      <div className="mx-auto flex w-full max-w-[1520px] min-w-0 flex-col gap-6">
        <header className="rounded-[2rem] border border-cyan-400/10 bg-slate-950/85 p-6 shadow-panel backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <p className="w-fit rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200">
                AskChip Local
              </p>
              <h1 className="text-3xl font-semibold tracking-tight text-white">Modern shell for canonical typed chat + push-to-talk</h1>
              <p className="max-w-3xl text-sm leading-6 text-slate-300">
                This frontend consumes the backend transcript contract directly for typed chat, push-to-talk voice input, streaming assistant deltas, deterministic Kokoro playback, and diagnostics.
              </p>
            </div>
            <div className="grid gap-2 text-right text-sm text-slate-300">
              <span>API state: {state.appError ? 'unavailable' : 'reachable'}</span>
              <span>Event stream: {state.connectionState}</span>
            </div>
          </div>
          {(state.appError || state.wsNotice || speech.speechError) && (
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {state.appError && (
                <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                  API unavailable: {state.appError}
                </div>
              )}
              {state.wsNotice && (
                <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                  {state.wsNotice}
                </div>
              )}
              {speech.speechError && (
                <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                  Assistant speech failed: {speech.speechError}
                </div>
              )}
            </div>
          )}
        </header>

        <section className="grid min-w-0 gap-6 xl:grid-cols-[minmax(260px,300px)_minmax(0,1fr)_minmax(280px,360px)]">
          <div className="min-w-0 space-y-6">
            <SessionList
              sessions={state.sessions}
              currentSessionId={state.currentSessionId}
              onCreate={actions.createSession}
              onSelect={actions.selectSession}
              onReload={actions.reloadTranscript}
              onDelete={actions.deleteSession}
              onOpenTranscript={openTranscriptHistory}
              onOpenVisualSession={openVisualSession}
            />
            <MicSetupPanel
              devices={audio.devices}
              selectedDeviceId={audio.selectedDeviceId}
              diagnostics={audio.diagnostics}
              webrtcDiagnostics={audio.webrtcDiagnostics}
              audioUnlocked={audio.audioUnlocked}
              onUnlock={audio.actions.unlockAudio}
              onRefresh={audio.actions.refreshDevices}
              onStart={() => audio.actions.startMicrophone()}
              onConnectWebRtc={audio.actions.connectWebRtc}
              onSelectDevice={audio.actions.selectDevice}
            />
            <UtilityRail collapsed={!showUtilityRail} onToggle={toggleUtilityRail} />
          </div>

          <div className="grid min-w-0 gap-6">
            <ChipStagePane state={state.topLevelState} modelName={modelName} config={state.config} />
            <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Live chat window</p>
                  <h2 className="text-lg font-semibold text-white">Floating conversation panel</h2>
                  <p className="mt-1 text-sm text-slate-300">
                    Status: {chatOpen ? 'Open' : 'Closed'}{state.currentSession ? ` • ${state.currentSession.title}` : ''}
                  </p>
                </div>
                {chatOpen ? (
                  <button
                    type="button"
                    onClick={() => setChatOpen(false)}
                    className="rounded-full border border-slate-700 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-200 transition hover:border-slate-500"
                  >
                    Close chat window
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => setChatOpen(true)}
                    className="rounded-full bg-cyan-400 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-950 transition hover:bg-cyan-300"
                  >
                    Open chat window
                  </button>
                )}
              </div>
            </section>
          </div>

          <div className="min-w-0">
            <DiagnosticsDrawer
              connectionState={state.connectionState}
              topLevelState={state.topLevelState}
              modelName={modelName}
              audioDiagnostics={audio.diagnostics}
              webrtcDiagnostics={audio.webrtcDiagnostics}
              events={state.events}
              timings={state.timings}
              config={state.config}
              readiness={state.readiness}
              speechState={{ activeMessageId: speech.activeMessageId, pendingMessageId: speech.pendingMessageId, speechError: speech.speechError }}
              turnLatencySummaries={state.turnLatencySummaries}
              readinessError={state.readinessError}
              collapsed={!showDiagnostics}
              onToggle={toggleDiagnostics}
            />
          </div>
        </section>
      </div>
      <FloatingChatWindow
        open={chatOpen}
        sessionTitle={state.currentSession?.title ?? null}
        messages={state.messages}
        empty={!state.currentSession || state.messages.length === 0}
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
      <FloatingTranscriptWindow
        open={historyOpen}
        sessionTitle={historySessionTitle}
        messages={historyMessages}
        loading={historyLoading}
        error={historyError}
        onClose={() => setHistoryOpen(false)}
      />
    </main>
  );
}
