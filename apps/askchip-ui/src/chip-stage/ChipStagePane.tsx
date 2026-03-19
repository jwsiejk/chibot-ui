import type { ConfigResponse, TurnState } from '../types/contract';

const STATE_COPY: Record<TurnState, { label: string; detail: string }> = {
  ready: {
    label: 'Ready',
    detail: 'The backend is idle and available for typed input.',
  },
  thinking: {
    label: 'Thinking',
    detail: 'An active typed turn is in progress on the backend.',
  },
  error: {
    label: 'Error',
    detail: 'The backend reported an assistant or model failure that needs attention.',
  },
};

export function ChipStagePane({
  state,
  modelName,
  config,
}: {
  state: TurnState | null;
  modelName: string | null;
  config: ConfigResponse | null;
}) {
  const resolved = state ? STATE_COPY[state] : null;

  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-6 shadow-panel backdrop-blur">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Chip stage</p>
          <h2 className="text-2xl font-semibold text-white">AskChip Local desktop shell</h2>
        </div>
        <div className="rounded-full border border-slate-700 px-3 py-1 text-xs uppercase tracking-[0.18em] text-slate-300">
          Typed chat only
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="flex min-h-[17rem] items-center justify-center rounded-[1.75rem] border border-cyan-400/20 bg-[radial-gradient(circle_at_top,_rgba(120,230,255,0.14),_transparent_35%),linear-gradient(180deg,rgba(9,14,29,0.95),rgba(5,9,20,0.98))]">
          <div className="space-y-4 text-center">
            <div className="mx-auto flex h-28 w-28 items-center justify-center rounded-full border border-cyan-400/30 bg-cyan-400/10 text-3xl font-semibold text-cyan-100 shadow-[0_0_60px_rgba(120,230,255,0.15)]">
              C
            </div>
            <div>
              <p className="text-sm uppercase tracking-[0.3em] text-slate-400">Contract state</p>
              <p className="mt-2 text-3xl font-semibold text-white">{resolved?.label ?? 'Unavailable'}</p>
            </div>
          </div>
        </div>

        <div className="space-y-4 rounded-[1.75rem] border border-slate-900 bg-slate-950/70 p-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-slate-400">Current state</p>
            <p className="mt-2 text-sm leading-6 text-slate-200">{resolved?.detail ?? 'No backend state is available yet.'}</p>
          </div>
          <dl className="space-y-3 text-sm text-slate-300">
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-800 px-4 py-3">
              <dt>Model</dt>
              <dd className="font-medium text-white">{modelName ?? config?.ollama_model ?? 'Unavailable'}</dd>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-800 px-4 py-3">
              <dt>Scope</dt>
              <dd className="font-medium text-white">Local-first</dd>
            </div>
            <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-3 text-xs leading-5 text-slate-400">
              Voice capture, wake word, WebRTC, and spoken transcript regions are intentionally not actionable in this phase.
            </div>
          </dl>
        </div>
      </div>
    </section>
  );
}
