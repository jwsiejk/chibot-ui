import type { TurnState } from '../types/contract';
import { TURN_STATE_COPY } from '../chip-stage/stateCopy';

const STATE_STYLE: Record<TurnState, { ring: string; glow: string; badge: string }> = {
  ready: {
    ring: 'ring-cyan-300/30',
    glow: 'shadow-[0_0_120px_rgba(56,189,248,0.22)]',
    badge: 'border-cyan-300/40 bg-cyan-300/15 text-cyan-100',
  },
  listening: {
    ring: 'ring-emerald-300/45',
    glow: 'shadow-[0_0_130px_rgba(52,211,153,0.28)]',
    badge: 'border-emerald-300/40 bg-emerald-300/15 text-emerald-100',
  },
  transcribing: {
    ring: 'ring-amber-300/45',
    glow: 'shadow-[0_0_130px_rgba(251,191,36,0.26)]',
    badge: 'border-amber-300/45 bg-amber-300/15 text-amber-100',
  },
  thinking: {
    ring: 'ring-violet-300/40',
    glow: 'shadow-[0_0_130px_rgba(196,181,253,0.26)]',
    badge: 'border-violet-300/40 bg-violet-300/15 text-violet-100',
  },
  speaking: {
    ring: 'ring-fuchsia-300/40',
    glow: 'shadow-[0_0_130px_rgba(244,114,182,0.3)]',
    badge: 'border-fuchsia-300/40 bg-fuchsia-300/15 text-fuchsia-100',
  },
  error: {
    ring: 'ring-rose-300/45',
    glow: 'shadow-[0_0_130px_rgba(251,113,133,0.3)]',
    badge: 'border-rose-300/45 bg-rose-300/15 text-rose-100',
  },
};

export function VisualSessionStage({
  state,
  assistantName,
}: {
  state: TurnState | null;
  assistantName: string;
}) {
  const resolvedState = state ?? 'ready';
  const copy = TURN_STATE_COPY[resolvedState];
  const style = STATE_STYLE[resolvedState];
  const speaking = resolvedState === 'speaking';

  return (
    <section className="relative flex min-h-[68vh] flex-col justify-between overflow-hidden rounded-[2.2rem] border border-slate-800/90 bg-[radial-gradient(circle_at_50%_16%,rgba(56,189,248,0.15),transparent_34%),linear-gradient(180deg,rgba(15,23,42,0.94),rgba(2,6,23,0.99))] px-6 py-8 lg:px-10 lg:py-10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_72%,rgba(59,130,246,0.13),transparent_44%)]" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-44 bg-gradient-to-t from-black/45 to-transparent" />
      <div className="relative z-10 flex items-start justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-400">Assistant stage</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-100">{assistantName}</h2>
        </div>
        <div className={`rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${style.badge}`}>
          {copy.label}
        </div>
      </div>
      <div className="relative z-10 flex flex-1 flex-col items-center justify-center gap-8 text-center">
        <div className={`relative rounded-full p-5 ring-2 ${style.ring} ${style.glow}`}>
          {speaking && (
            <span
              aria-hidden
              className="absolute inset-0 rounded-full border border-fuchsia-300/40 animate-ping"
            />
          )}
          <div className="flex h-48 w-48 items-center justify-center rounded-full border border-white/15 bg-gradient-to-br from-slate-700 to-slate-900 text-6xl font-semibold text-cyan-100">
            {assistantName.slice(0, 1).toUpperCase()}
          </div>
        </div>
        <div className="max-w-xl space-y-2">
          <p className="text-lg leading-8 text-slate-100">{copy.detail}</p>
        </div>
      </div>
      <div className="relative z-10 rounded-2xl border border-slate-700/80 bg-slate-950/60 px-5 py-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-cyan-100/85">Live lower-third</p>
        <div className="mt-1 flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-white">{assistantName} · Local Assistant</p>
          <p className="text-xs text-slate-300">State: {copy.label}</p>
        </div>
      </div>
    </section>
  );
}
