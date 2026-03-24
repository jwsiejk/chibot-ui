import { useEffect, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';
import { MessageGroup } from './MessageGroup';

interface FloatingTranscriptWindowProps {
  open: boolean;
  sessionTitle: string | null;
  messages: TranscriptMessage[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

const WINDOW_WIDTH = 480;
const WINDOW_HEIGHT = 540;

export function FloatingTranscriptWindow({
  open,
  sessionTitle,
  messages,
  loading,
  error,
  onClose,
}: FloatingTranscriptWindowProps) {
  const [position, setPosition] = useState({ x: 48, y: 96 });
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const draggingRef = useRef(false);

  useEffect(() => {
    const nextX = Math.max(24, window.innerWidth - WINDOW_WIDTH - 24);
    const nextY = 96;
    setPosition({ x: nextX, y: nextY });
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

  if (!open) {
    return null;
  }

  return (
    <aside
      className="fixed z-40 flex h-[540px] w-[480px] flex-col rounded-3xl border border-cyan-400/30 bg-slate-950/95 shadow-2xl backdrop-blur"
      style={{ left: position.x, top: position.y }}
      aria-label="Transcript history window"
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
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200/80">Transcript history</p>
          <h3 className="text-sm font-semibold text-white">{sessionTitle ?? 'Session transcript'}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-full border border-slate-700 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-slate-300 transition hover:border-slate-500 hover:text-white"
        >
          Close
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {loading ? (
          <div className="flex h-full min-h-[14rem] items-center justify-center rounded-2xl border border-dashed border-slate-700 text-sm text-slate-300">
            Loading transcript history…
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div>
        ) : messages.length === 0 ? (
          <div className="flex h-full min-h-[14rem] items-center justify-center rounded-2xl border border-dashed border-slate-700 text-sm text-slate-300">
            This session has no transcript history.
          </div>
        ) : (
          <MessageGroup messages={messages} />
        )}
      </div>
    </aside>
  );
}
