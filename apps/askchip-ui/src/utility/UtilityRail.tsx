export function UtilityRail({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Utility rail</p>
          <h2 className="text-lg font-semibold text-white">Reserved future agent surface</h2>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="rounded-full border border-slate-700 bg-slate-950/60 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-cyan-400/40 hover:text-white"
          aria-expanded={!collapsed}
        >
          {collapsed ? 'Expand' : 'Collapse'}
        </button>
      </header>
      {!collapsed && (
        <div className="rounded-[1.5rem] border border-dashed border-slate-800 px-4 py-6 text-sm leading-6 text-slate-400">
          Reserved for future local-only agent utilities. This area is intentionally non-actionable in the typed-chat shell.
        </div>
      )}
    </section>
  );
}
