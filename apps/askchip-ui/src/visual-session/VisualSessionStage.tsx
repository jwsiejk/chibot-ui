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

export function VisualSessionStage({ state }: { state: TurnState | null }) {
  const resolvedState = state ?? 'ready';
  const copy = TURN_STATE_COPY[resolvedState];
  const style = STATE_STYLE[resolvedState];

  return (
    <section className="relative flex min-h-[58vh] flex-col items-center justify-center overflow-hidden rounded-[2.2rem] border border-slate-800 bg-[radial-gradient(circle_at_50%_18%,rgba(56,189,248,0.12),transparent_38%),linear-gradient(180deg,rgba(15,23,42,0.92),rgba(2,6,23,0.98))] px-6 py-10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_72%,rgba(59,130,246,0.12),transparent_44%)]" />
      <div className="relative z-10 flex flex-col items-center gap-6 text-center">
        <div className={`relative rounded-full p-3 ring-2 ${style.ring} ${style.glow}`}>
          <div className="flex h-40 w-40 items-center justify-center rounded-full border border-white/15 bg-gradient-to-br from-slate-700 to-slate-900 text-6xl font-semibold text-cyan-100">
            C
          </div>
          <div className={`absolute -bottom-2 left-1/2 -translate-x-1/2 rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${style.badge}`}>
            {copy.label}
          </div>
        </div>
        <div className="max-w-xl space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">AskChip assistant stage</p>
          <p className="text-base leading-7 text-slate-200">{copy.detail}</p>
        </div>
      </div>
    </section>
  );
}
