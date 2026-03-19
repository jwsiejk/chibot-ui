import type { TranscriptMessage } from '../types/contract';
import { MessageGroup } from './MessageGroup';

interface TranscriptPaneProps {
  messages: TranscriptMessage[];
  empty: boolean;
}

export function TranscriptPane({ messages, empty }: TranscriptPaneProps) {
  return (
    <section className="flex min-h-[28rem] flex-col rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Canonical transcript</p>
          <h2 className="text-xl font-semibold text-white">Typed chat history</h2>
        </div>
        <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">{messages.length} messages</span>
      </header>
      <div className="flex-1 overflow-auto rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
        {empty ? (
          <div className="flex h-full min-h-[18rem] items-center justify-center rounded-[1.25rem] border border-dashed border-slate-800 text-center text-sm leading-6 text-slate-400">
            <div>
              <p className="font-medium text-slate-200">No transcript yet.</p>
              <p>Send a typed turn to render canonical user and assistant messages from the backend.</p>
            </div>
          </div>
        ) : (
          <MessageGroup messages={messages} />
        )}
      </div>
    </section>
  );
}
