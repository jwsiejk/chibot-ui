import { useEffect, useRef } from 'react';
import { Composer } from '../composer/Composer';
import { MessageGroup } from '../transcript/MessageGroup';
import type { TranscriptMessage } from '../types/contract';
import { VoiceInputPanel } from '../audio/VoiceInputPanel';

interface VoiceDraftState {
  mode: 'listening' | 'transcribing';
  text: string;
  durationMs: number | null;
}

interface ChatWindowProps {
  messages: TranscriptMessage[];
  empty: boolean;
  disabledReason: string | null;
  pending: boolean;
  onSend: (text: string) => Promise<void>;
  voiceDisabled: boolean;
  voiceDisabledReason: string | null;
  liveDraft: VoiceDraftState | null;
  onPressStart: () => Promise<void>;
  onPressEnd: () => Promise<void>;
  onPressCancel: () => Promise<void>;
}

export function ChatWindow({
  messages,
  empty,
  disabledReason,
  pending,
  onSend,
  voiceDisabled,
  voiceDisabledReason,
  liveDraft,
  onPressStart,
  onPressEnd,
  onPressCancel,
}: ChatWindowProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const scrollNode = scrollRef.current;
    const endNode = endRef.current;
    if (!scrollNode || !endNode) {
      return;
    }

    const nearBottom = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight < 80;
    if (nearBottom) {
      endNode.scrollIntoView({ block: 'end' });
    }
  }, [messages]);

  return (
    <section className="flex min-h-[34rem] flex-col rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Conversation</p>
          <h2 className="text-xl font-semibold text-white">Chat</h2>
        </div>
        <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">{messages.length} messages</span>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-auto rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
        {empty ? (
          <div className="flex h-full min-h-[18rem] items-center justify-center rounded-[1.25rem] border border-dashed border-slate-800 text-center text-sm leading-6 text-slate-400">
            <div>
              <p className="font-medium text-slate-200">No transcript yet.</p>
              <p>Send a typed turn or release a push-to-talk voice turn to render canonical user and assistant messages from the backend.</p>
            </div>
          </div>
        ) : (
          <>
            <MessageGroup messages={messages} />
            <div ref={endRef} />
          </>
        )}
      </div>

      <div className="mt-4 space-y-4 rounded-[1.5rem] border border-slate-900 bg-slate-950/40 p-4">
        <Composer
          compact
          disabledReason={disabledReason}
          pending={pending}
          onSend={onSend}
        />
        <VoiceInputPanel
          compact
          disabled={voiceDisabled}
          disabledReason={voiceDisabledReason}
          liveDraft={liveDraft}
          onPressStart={onPressStart}
          onPressEnd={onPressEnd}
          onPressCancel={onPressCancel}
        />
      </div>
    </section>
  );
}
