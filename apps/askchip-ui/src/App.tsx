const regionClassName =
  'rounded-2xl border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur';

const shellSections = [
  {
    title: 'Chip stage',
    description: 'Reserved presentation surface for the local AskChip avatar and response states.',
  },
  {
    title: 'Transcript pane',
    description: 'Reserved read-only conversation transcript area for future on-device sessions.',
  },
  {
    title: 'Composer',
    description: 'Reserved input composer region for local text prompts and submission controls.',
  },
  {
    title: 'Diagnostics drawer',
    description: 'Reserved diagnostics surface for local runtime health, events, and instrumentation.',
  },
  {
    title: 'Utility rail',
    description: 'Reserved utility rail for future local-only affordances and shortcuts.',
  },
] as const;

function App() {
  return (
    <main className="min-h-screen bg-surface px-6 py-8 text-slate-100">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <header className="flex flex-col gap-3 rounded-3xl border border-cyan-400/10 bg-slate-950/80 p-8 shadow-panel backdrop-blur">
          <span className="w-fit rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200">
            AskChip Local Foundation
          </span>
          <div className="space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight text-white">Local-first UI shell for AskChip v1</h1>
            <p className="max-w-3xl text-sm leading-6 text-slate-300">
              This isolated foundation intentionally exposes structure only. Interactive model orchestration,
              diagnostics behavior, and advanced media capabilities remain out of scope for this phase.
            </p>
          </div>
        </header>

        <section className="grid gap-6 lg:grid-cols-[1.8fr_1fr]">
          <div className="grid gap-6">
            {shellSections.slice(0, 3).map((section) => (
              <article key={section.title} className={regionClassName}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-lg font-medium text-white">{section.title}</h2>
                  <span className="rounded-full border border-slate-700 px-2 py-1 text-xs uppercase tracking-wide text-slate-400">
                    Placeholder
                  </span>
                </div>
                <p className="text-sm leading-6 text-slate-400">{section.description}</p>
              </article>
            ))}
          </div>

          <aside className="grid gap-6">
            {shellSections.slice(3).map((section) => (
              <article key={section.title} className={regionClassName}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-lg font-medium text-white">{section.title}</h2>
                  <span className="rounded-full border border-slate-700 px-2 py-1 text-xs uppercase tracking-wide text-slate-400">
                    Placeholder
                  </span>
                </div>
                <p className="text-sm leading-6 text-slate-400">{section.description}</p>
              </article>
            ))}
          </aside>
        </section>
      </div>
    </main>
  );
}

export default App;
