import type { SessionRecord } from '../types/contract';

function formatRelative(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

export function SessionList({
  sessions,
  currentSessionId,
  onCreate,
  onSelect,
  onReload,
}: {
  sessions: SessionRecord[];
  currentSessionId: string | null;
  onCreate: () => Promise<void>;
  onSelect: (sessionId: string) => Promise<void>;
  onReload: () => Promise<void>;
}) {
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Sessions</p>
          <h2 className="text-lg font-semibold text-white">Local session list</h2>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void onReload()} className="rounded-full border border-slate-700 px-3 py-2 text-xs text-slate-300">
            Reload
          </button>
          <button type="button" onClick={() => void onCreate()} className="rounded-full bg-cyan-400 px-3 py-2 text-xs font-semibold text-slate-950">
            New session
          </button>
        </div>
      </div>
      <div className="space-y-2">
        {sessions.length === 0 ? (
          <div className="rounded-[1.5rem] border border-dashed border-slate-800 px-4 py-6 text-sm text-slate-400">
            No local sessions yet. Create a session to start typed chat.
          </div>
        ) : (
          sessions.map((session) => {
            const active = session.id === currentSessionId;
            return (
              <button
                key={session.id}
                type="button"
                onClick={() => void onSelect(session.id)}
                className={[
                  'w-full rounded-[1.5rem] border px-4 py-3 text-left transition',
                  active ? 'border-cyan-400/40 bg-cyan-400/10' : 'border-slate-800 bg-slate-950/50 hover:border-slate-700',
                ].join(' ')}
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="font-medium text-white">{session.title}</p>
                  <span className="rounded-full border border-slate-700 px-2 py-1 text-[11px] uppercase tracking-[0.2em] text-slate-400">
                    {session.status}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between gap-3 text-xs text-slate-400">
                  <span>{formatRelative(session.updated_at)}</span>
                  <span>{session.last_message_at ? 'Has transcript' : 'Empty transcript'}</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </section>
  );
}
