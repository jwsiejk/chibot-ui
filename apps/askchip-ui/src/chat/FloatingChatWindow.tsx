import { useEffect, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';

interface VoiceDraftState {
  mode: 'listening' | 'transcribing';
  text: string;
  durationMs: number | null;
}

interface FloatingChatWindowProps {
  open: boolean;
  sessionTitle: string | null;
  messages: TranscriptMessage[];
  empty: boolean;
  disabledReason: string | null;
  pending: boolean;
  voiceDisabled: boolean;
  voiceDisabledReason: string | null;
  liveDraft: VoiceDraftState | null;
  onSend: (text: string) => Promise<void>;
  onPressStart: () => Promise<void>;
  onPressEnd: () => Promise<void>;
  onPressCancel: () => Promise<void>;
  onClose: () => void;
}

const WINDOW_WIDTH = 460;
const WINDOW_HEIGHT = 620;

function formatStamp(value: string | null): string {
  if (!value) {
    return 'now';
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

function formatDraftStatus(liveDraft: VoiceDraftState | null): string | null {
  if (!liveDraft) {
    return null;
  }
  if (liveDraft.mode === 'listening') {
    return 'Listening…';
  }
  return 'Transcribing…';
}

export function FloatingChatWindow({
  open,
  sessionTitle,
  messages,
  empty,
  disabledReason,
  pending,
  voiceDisabled,
  voiceDisabledReason,
  liveDraft,
  onSend,
  onPressStart,
  onPressEnd,
  onPressCancel,
  onClose,
}: FloatingChatWindowProps) {
  const [position, setPosition] = useState({ x: 40, y: 80 });
  const [text, setText] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const draggingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const micPointerIdRef = useRef<number | null>(null);

  const canSend = !disabledReason && text.trim().length > 0;
  const draftStatus = formatDraftStatus(liveDraft);

  useEffect(() => {
    const nextX = Math.max(24, window.innerWidth - WINDOW_WIDTH - 24);
    setPosition({ x: nextX, y: 80 });
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (!draggingRef.current) {
        return;
      }

      const maxX = Math.max(24, window.innerWidth - WINDOW_WIDTH - 24);
      const maxY = Math.max(24, window.innerHeight - WINDOW_HEIGHT - 24);
      const nextX = Math.min(maxX, Math.max(24, event.clientX - dragOffsetRef.current.x));
      const nextY = Math.min(maxY, Math.max(24, event.clientY - dragOffsetRef.current.y));
      setPosition({ x: nextX, y: nextY });
    };

    const handlePointerUp = () => {
      draggingRef.current = false;
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const scrollNode = scrollRef.current;
    const endNode = endRef.current;
    if (!scrollNode || !endNode) {
      return;
    }

    const nearBottom = scrollNode.scrollHeight - scrollNode.scrollTop - scrollNode.clientHeight < 80;
    if (nearBottom) {
      endNode.scrollIntoView({ block: 'end' });
    }
  }, [messages, open]);

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
      setLocalError(error instanceof Error ? error.message : 'Failed to send message.');
    }
  }

  if (!open) {
    return null;
  }

  return (
    <aside
      className="fixed z-30 flex h-[620px] w-[460px] flex-col rounded-3xl border border-cyan-400/30 bg-slate-950/95 shadow-2xl backdrop-blur"
      style={{ left: position.x, top: position.y }}
      aria-label="Live chat window"
    >
      <header
        className="flex cursor-move items-center justify-between gap-3 rounded-t-3xl border-b border-slate-800 bg-slate-900/80 px-4 py-3"
        onPointerDown={(event) => {
          draggingRef.current = true;
          dragOffsetRef.current = {
            x: event.clientX - position.x,
            y: event.clientY - position.y,
          };
        }}
      >
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200/80">Live chat</p>
          <h3 className="text-sm font-semibold text-white">{sessionTitle ?? 'No session selected'}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-full border border-slate-700 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          Close
        </button>
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto p-4">
        {empty ? (
          <div className="flex h-full min-h-[14rem] items-center justify-center rounded-2xl border border-dashed border-slate-700 px-4 text-center text-sm text-slate-300">
            Start chatting by typing a message or using push-to-talk.
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map((message) => {
              const isUser = message.role === 'user';
              return (
                <div key={message.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                  <article
                    className={[
                      'max-w-[85%] rounded-3xl px-4 py-3 text-sm leading-6 shadow-panel',
                      isUser ? 'bg-cyan-400/15 text-cyan-50' : 'bg-slate-900 text-slate-100',
                    ].join(' ')}
                  >
                    <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-300">
                      {isUser ? 'You' : 'AskChip'} · {formatStamp(message.completed_at ?? message.committed_at ?? message.created_at)}
                    </p>
                    <p className="whitespace-pre-wrap">{message.text || ' '}</p>
                  </article>
                </div>
              );
            })}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <div className="border-t border-slate-800 bg-slate-900/70 p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="Type a message..."
            className="min-h-[3.25rem] flex-1 resize-none rounded-2xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-400/40"
            disabled={pending}
          />
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!canSend || pending}
            className="rounded-2xl bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {pending ? 'Sending…' : 'Send'}
          </button>
          <button
            type="button"
            disabled={voiceDisabled}
            onPointerDown={(event) => {
              if (voiceDisabled || micPointerIdRef.current !== null) {
                return;
              }
              micPointerIdRef.current = event.pointerId;
              event.currentTarget.setPointerCapture(event.pointerId);
              void onPressStart();
            }}
            onPointerUp={(event) => {
              if (micPointerIdRef.current !== event.pointerId) {
                return;
              }
              micPointerIdRef.current = null;
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
              void onPressEnd();
            }}
            onPointerCancel={(event) => {
              if (micPointerIdRef.current !== event.pointerId) {
                return;
              }
              micPointerIdRef.current = null;
              void onPressCancel();
            }}
            onLostPointerCapture={() => {
              if (micPointerIdRef.current === null) {
                return;
              }
              micPointerIdRef.current = null;
              void onPressCancel();
            }}
            className="rounded-2xl border border-cyan-400/40 bg-slate-950 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:border-cyan-300 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
            aria-label="Push to talk"
            title="Hold to talk"
          >
            Mic
          </button>
        </div>
        <div className="mt-2 min-h-5 text-xs text-slate-400">
          {localError ?? disabledReason ?? voiceDisabledReason ?? draftStatus ?? 'Use Send for typing or hold Mic for voice.'}
        </div>
      </div>
    </aside>
  );
}
