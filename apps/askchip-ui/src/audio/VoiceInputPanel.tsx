interface VoiceDraftState {
  mode: 'listening' | 'transcribing';
  text: string;
  durationMs: number | null;
}

function formatDuration(durationMs: number | null): string {
  if (!durationMs || durationMs < 1000) {
    return '<1s';
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

export function VoiceInputPanel({
  disabled,
  disabledReason,
  liveDraft,
  onPressStart,
  onPressEnd,
}: {
  disabled: boolean;
  disabledReason: string | null;
  liveDraft: VoiceDraftState | null;
  onPressStart: () => Promise<void>;
  onPressEnd: () => Promise<void>;
}) {
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Voice input</p>
          <h2 className="text-lg font-semibold text-white">Push to talk</h2>
        </div>
        <div className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300">Release commits</div>
      </header>
      <div className="space-y-4 text-sm text-slate-300">
        <button
          type="button"
          disabled={disabled}
          onPointerDown={() => void onPressStart()}
          onPointerUp={() => void onPressEnd()}
          onPointerLeave={() => void onPressEnd()}
          onKeyDown={(event) => {
            if (event.repeat) {
              return;
            }
            if (event.key === ' ' || event.key === 'Enter') {
              event.preventDefault();
              void onPressStart();
            }
          }}
          onKeyUp={(event) => {
            if (event.key === ' ' || event.key === 'Enter') {
              event.preventDefault();
              void onPressEnd();
            }
          }}
          className="w-full rounded-[1.75rem] border border-cyan-400/40 bg-slate-950/70 px-5 py-5 text-left transition hover:border-cyan-300 disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
        >
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-base font-semibold text-white">Hold to record a voice turn</p>
              <p className="mt-1 text-sm text-slate-400">Press and hold to capture from the selected microphone, then release to upload for faster-whisper transcription.</p>
            </div>
            <div className="rounded-full bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950">PTT</div>
          </div>
        </button>

        <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
          {liveDraft ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200/80">{liveDraft.mode}</span>
                <span className="text-xs text-slate-400">{formatDuration(liveDraft.durationMs)}</span>
              </div>
              <p className="text-sm leading-6 text-slate-200">{liveDraft.text}</p>
              <p className="text-xs text-slate-400">This draft is local-only until the backend emits a committed voice transcript row.</p>
            </div>
          ) : (
            <p className="text-sm leading-6 text-slate-400">Voice drafts stay separate from the typed composer. The canonical transcript updates only after release, STT, and backend commit.</p>
          )}
        </div>

        <div className="min-h-5 text-xs text-slate-400">{disabledReason ?? 'Voice turns use the same canonical transcript and assistant response path as typed chat.'}</div>
      </div>
    </section>
  );
}
