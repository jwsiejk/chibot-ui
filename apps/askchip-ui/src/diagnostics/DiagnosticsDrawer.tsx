import type { ConnectionState } from '../api/events';
import type { EventRecord, TimingRecord, TurnState } from '../types/contract';

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}

export function DiagnosticsDrawer({
  connectionState,
  topLevelState,
  modelName,
  events,
  timings,
  collapsed,
  onToggle,
}: {
  connectionState: ConnectionState;
  topLevelState: TurnState | null;
  modelName: string | null;
  events: EventRecord[];
  timings: TimingRecord[];
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Diagnostics</p>
          <h2 className="text-lg font-semibold text-white">Backend-driven runtime details</h2>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="rounded-full border border-slate-700 bg-slate-950/60 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-cyan-400/40 hover:text-white"
          aria-expanded={!collapsed}
        >
          {collapsed ? 'Expand' : 'Collapse'}
        </button>
      </header>
      {!collapsed && (
        <div className="space-y-4">
          <dl className="grid gap-3 text-sm text-slate-300">
            <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
              <dt>WebSocket</dt>
              <dd className="font-medium text-white">{connectionState}</dd>
            </div>
            <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
              <dt>Top-level state</dt>
              <dd className="font-medium text-white">{topLevelState ?? 'Unavailable'}</dd>
            </div>
            <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
              <dt>Current model</dt>
              <dd className="truncate pl-3 font-medium text-white">{modelName ?? 'Unavailable'}</dd>
            </div>
          </dl>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Recent events</h3>
            <div className="space-y-2 text-xs text-slate-300">
              {events.length === 0 ? (
                <p className="text-slate-500">No backend events received yet.</p>
              ) : (
                [...events].slice(-8).reverse().map((event) => (
                  <div key={event.id} className="rounded-xl border border-slate-800 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-white">{event.type}</span>
                      <span className="text-slate-500">{formatTime(event.created_at)}</span>
                    </div>
                    <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">
                      {JSON.stringify(event.payload, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Recent timings</h3>
            <div className="space-y-2 text-xs text-slate-300">
              {timings.length === 0 ? (
                <p className="text-slate-500">No timing records are available yet.</p>
              ) : (
                [...timings].slice(-6).reverse().map((timing) => (
                  <div key={timing.id} className="rounded-xl border border-slate-800 px-3 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-white">{timing.phase}</span>
                      <span className="text-slate-500">{timing.duration_ms ?? 'pending'} ms</span>
                    </div>
                    <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">
                      {JSON.stringify(timing.meta, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
