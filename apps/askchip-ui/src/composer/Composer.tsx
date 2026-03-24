import { useState } from 'react';

export function Composer({
  disabledReason,
  pending,
  onSend,
  compact = false,
}: {
  disabledReason: string | null;
  pending: boolean;
  onSend: (text: string) => Promise<void>;
  compact?: boolean;
}) {
  const [text, setText] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const canSend = !disabledReason && text.trim().length > 0;

  async function submit() {
    const nextText = text.trim();
    if (!nextText) {
      return;
    }
    setLocalError(null);
    try {
      await onSend(nextText);
      setText('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Failed to send typed turn.');
    }
  }

  return (
    <section className={compact ? '' : 'sticky bottom-0 rounded-[2rem] border border-slate-800 bg-slate-950/90 p-4 shadow-panel backdrop-blur'}>
      {!compact && (
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Composer</p>
            <h2 className="text-lg font-semibold text-white">Typed turn input</h2>
          </div>
          <div className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">Enter ↵ to send</div>
        </div>
      )}
      <div className="flex gap-3">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="Type a message for AskChip..."
          className="min-h-[5.5rem] flex-1 resize-none rounded-[1.5rem] border border-slate-800 bg-panel px-4 py-3 text-sm text-slate-100 outline-none transition focus:border-cyan-400/40"
          disabled={pending}
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!canSend || pending}
          className="rounded-[1.5rem] bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {pending ? 'Sending…' : 'Send'}
        </button>
      </div>
      <div className="mt-3 min-h-5 text-sm text-slate-400">
        {localError ?? disabledReason ?? 'Typed draft text remains local to this composer until the backend commits a canonical transcript message.'}
      </div>
    </section>
  );
}
