import { ChipStagePane } from '../chip-stage/ChipStagePane';
import { Composer } from '../composer/Composer';
import { DiagnosticsDrawer } from '../diagnostics/DiagnosticsDrawer';
import { SessionList } from '../sessions/SessionList';
import { useAskChipController } from '../state/useAskChipController';
import { TranscriptPane } from '../transcript/TranscriptPane';
import { UtilityRail } from '../utility/UtilityRail';

function findActiveModelName(messages: ReturnType<typeof useAskChipController>['state']['messages']): string | null {
  const assistant = [...messages].reverse().find((message) => message.role === 'assistant');
  const model = assistant?.metadata.model;
  return typeof model === 'string' ? model : null;
}

export function AskChipShell() {
  const { state, actions } = useAskChipController();
  const modelName = findActiveModelName(state.messages) ?? state.config?.ollama_model ?? null;

  return (
    <main className="min-h-screen bg-surface px-6 py-8 text-slate-100">
      <div className="mx-auto flex max-w-[1520px] flex-col gap-6">
        <header className="rounded-[2rem] border border-cyan-400/10 bg-slate-950/85 p-6 shadow-panel backdrop-blur">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <p className="w-fit rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200">
                AskChip Local
              </p>
              <h1 className="text-3xl font-semibold tracking-tight text-white">Modern shell for canonical typed chat</h1>
              <p className="max-w-3xl text-sm leading-6 text-slate-300">
                This frontend consumes the backend transcript contract directly for typed chat, sessions, streaming assistant deltas, and diagnostics.
              </p>
            </div>
            <div className="grid gap-2 text-right text-sm text-slate-300">
              <span>API state: {state.appError ? 'unavailable' : 'reachable'}</span>
              <span>Event stream: {state.connectionState}</span>
            </div>
          </div>
          {(state.appError || state.wsNotice) && (
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
            </div>
          )}
        </header>

        <section className="grid gap-6 xl:grid-cols-[300px_minmax(0,1fr)_360px]">
          <div className="space-y-6">
            <SessionList
              sessions={state.sessions}
              currentSessionId={state.currentSessionId}
              onCreate={actions.createSession}
              onSelect={actions.selectSession}
              onReload={actions.reloadTranscript}
            />
            <UtilityRail />
          </div>

          <div className="grid gap-6">
            <ChipStagePane state={state.topLevelState} modelName={modelName} config={state.config} />
            <TranscriptPane messages={state.messages} empty={!state.currentSession || state.messages.length === 0} />
            <Composer
              disabledReason={state.sendingDisabledReason}
              pending={state.pendingTurn}
              onSend={actions.sendTurn}
            />
          </div>

          <DiagnosticsDrawer
            connectionState={state.connectionState}
            topLevelState={state.topLevelState}
            modelName={modelName}
            events={state.events}
            timings={state.timings}
          />
        </section>
      </div>
    </main>
  );
}
