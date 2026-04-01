type VisualSessionViewProps = {
  sessionId: string;
};

export function VisualSessionView({ sessionId }: VisualSessionViewProps) {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-8 text-slate-100">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <header className="rounded-[2rem] border border-cyan-400/20 bg-slate-900/80 p-6 shadow-panel">
          <p className="w-fit rounded-full border border-cyan-400/30 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100">
            Visual Session
          </p>
          <h1 className="mt-3 text-3xl font-semibold text-white">Session {sessionId}</h1>
          <p className="mt-2 text-sm text-slate-300">
            This route is scaffolded for a dedicated Zoom-like meeting surface and is ready for incremental UI work.
          </p>
        </header>

        <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-6">
          <div className="aspect-video rounded-[1.5rem] border border-dashed border-cyan-300/40 bg-slate-900/60 p-6">
            <p className="text-sm text-slate-300">Primary visual meeting canvas placeholder for session {sessionId}.</p>
          </div>
          <div className="mt-4">
            <a
              href="/"
              className="inline-flex rounded-full border border-slate-700 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-200 transition hover:border-slate-500"
            >
              Back to AskChip Shell
            </a>
          </div>
        </section>
      </div>
    </main>
  );
}
