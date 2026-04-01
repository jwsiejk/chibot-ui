export function ExpertDeskLandingView() {
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,#eef2ff_0%,#e2e8f0_40%,#f8fafc_100%)] px-6 py-8 text-slate-900">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8">
        <header className="rounded-3xl border border-slate-200 bg-white/90 p-8 shadow-xl shadow-slate-300/40">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-indigo-700">AskChip Frontstage Demo</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight text-slate-950">Expert Desk</h1>
          <p className="mt-4 max-w-3xl text-base leading-7 text-slate-700">
            A focused frontstage experience for issue triage and expert routing. This shell is intentionally separate from
            the backstage AskChip Local runtime workspace.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a
              href="/demo/intake"
              className="inline-flex rounded-full bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500"
            >
              Start intake
            </a>
            <a
              href="/"
              className="inline-flex rounded-full border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-400"
            >
              Back to backstage shell
            </a>
          </div>
        </header>

        <section className="grid gap-4 md:grid-cols-3">
          {[
            ['1. Clarify issue', 'Capture category, environment, and urgency before any handoff.'],
            ['2. Route by specialty', 'Match to the right expert profile based on the stated need.'],
            ['3. Continue in-session', 'Carry structured intake context into the live Expert Desk conversation.'],
          ].map(([title, copy]) => (
            <article key={title} className="rounded-2xl border border-slate-200 bg-white/85 p-5 shadow-sm">
              <h2 className="text-base font-semibold text-slate-900">{title}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">{copy}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
