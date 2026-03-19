import type { TranscriptMessage } from '../types/contract';

function formatStamp(value: string | null): string {
  if (!value) {
    return 'pending';
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    month: 'short',
    day: 'numeric',
  }).format(new Date(value));
}

export function MessageRow({ message }: { message: TranscriptMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <article
        className={[
          'max-w-[85%] rounded-3xl border px-4 py-3 shadow-panel',
          isUser
            ? 'border-cyan-400/30 bg-cyan-400/12 text-cyan-50'
            : 'border-slate-800 bg-slate-950/80 text-slate-100',
        ].join(' ')}
      >
        <header className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-slate-400">
          <span>{message.role}</span>
          <span className="text-slate-600">•</span>
          <span>{message.source}</span>
          <span className="text-slate-600">•</span>
          <span>{message.status}</span>
        </header>
        <p className="whitespace-pre-wrap text-sm leading-6 text-inherit">{message.text || ' '}</p>
        <footer className="mt-3 flex items-center justify-between gap-3 text-xs text-slate-400">
          <span>{message.modality}</span>
          <span>{formatStamp(message.completed_at ?? message.committed_at ?? message.created_at)}</span>
        </footer>
      </article>
    </div>
  );
}
