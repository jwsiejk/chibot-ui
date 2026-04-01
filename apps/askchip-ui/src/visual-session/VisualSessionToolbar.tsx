interface VisualSessionToolbarProps {
  chatOpen: boolean;
  voiceDisabled: boolean;
  voiceActive: boolean;
  stopDisabled: boolean;
  onToggleChat: () => void;
  onVoice: () => void;
  onStop: () => void;
}

export function VisualSessionToolbar({
  chatOpen,
  voiceDisabled,
  voiceActive,
  stopDisabled,
  onToggleChat,
  onVoice,
  onStop,
}: VisualSessionToolbarProps) {
  return (
    <div className="fixed bottom-7 left-1/2 z-20 -translate-x-1/2">
      <div className="flex items-center gap-2 rounded-full border border-slate-700/80 bg-slate-950/90 px-3 py-2 shadow-[0_12px_46px_rgba(2,6,23,0.65)] backdrop-blur">
        <button
          type="button"
          onClick={onToggleChat}
          className="rounded-full border border-slate-700 bg-slate-900/90 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-100 transition hover:border-slate-500 hover:bg-slate-800/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/60"
        >
          {chatOpen ? 'Close chat' : 'Open chat'}
        </button>
        <button
          type="button"
          onClick={onVoice}
          disabled={voiceDisabled}
          className="rounded-full bg-cyan-400 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-950 transition hover:bg-cyan-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          {voiceActive ? 'Release' : 'Voice'}
        </button>
        <button
          type="button"
          onClick={onStop}
          disabled={stopDisabled}
          className="rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-amber-100 transition hover:border-amber-300/60 hover:bg-amber-300/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/60 disabled:cursor-not-allowed disabled:opacity-45"
        >
          Stop
        </button>
        <a
          href="/"
          className="rounded-full border border-rose-300/35 bg-rose-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-rose-100 transition hover:border-rose-300/60 hover:bg-rose-400/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"
        >
          End session
        </a>
      </div>
    </div>
  );
}
