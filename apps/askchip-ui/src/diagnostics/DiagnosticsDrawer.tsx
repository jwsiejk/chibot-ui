import type { ConnectionState } from '../api/events';
import type { AudioDiagnosticsSnapshot } from '../audio/types';
import type { ConfigResponse, EventRecord, ReadinessResponse, TimingRecord, TurnState } from '../types/contract';
import type { WebRtcDiagnosticsSnapshot } from '../webrtc/types';

function formatTime(value: string | null): string {
  if (!value) {
    return 'Unavailable';
  }
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(value));
}

function summarizeStopReason(events: EventRecord[]): string {
  const stopEvent = [...events].reverse().find((event) => event.type === 'tts.stopped' || event.type === 'ptt.stopped');
  if (!stopEvent) {
    return 'None observed';
  }
  const reason = typeof stopEvent.payload.reason === 'string' ? stopEvent.payload.reason : null;
  return reason ? `${stopEvent.type}: ${reason}` : stopEvent.type;
}

export function DiagnosticsDrawer({ connectionState, topLevelState, modelName, audioDiagnostics, webrtcDiagnostics, events, timings, config, readiness, speechState, collapsed, onToggle }: {
  connectionState: ConnectionState;
  topLevelState: TurnState | null;
  modelName: string | null;
  audioDiagnostics: AudioDiagnosticsSnapshot;
  webrtcDiagnostics: WebRtcDiagnosticsSnapshot;
  events: EventRecord[];
  timings: TimingRecord[];
  config: ConfigResponse | null;
  readiness: ReadinessResponse | null;
  speechState: { activeMessageId: string | null; pendingMessageId: string | null; speechError: string | null };
  collapsed: boolean;
  onToggle: () => void;
}) {
  const groupedTimings = [...timings].slice().reverse();
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Diagnostics</p>
          <h2 className="text-lg font-semibold text-white">Backend-driven runtime details</h2>
        </div>
        <button type="button" onClick={onToggle} className="rounded-full border border-slate-700 bg-slate-950/60 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-cyan-400/40 hover:text-white" aria-expanded={!collapsed}>{collapsed ? 'Expand' : 'Collapse'}</button>
      </header>
      {!collapsed && (
        <div className="space-y-4">
          <dl className="grid gap-3 text-sm text-slate-300">
            {[
              ['WebSocket', connectionState],
              ['Top-level state', topLevelState ?? 'Unavailable'],
              ['Current model', modelName ?? 'Unavailable'],
              ['Selected mic', audioDiagnostics.selectedDeviceLabel ?? audioDiagnostics.selectedDeviceId ?? 'Unavailable'],
              ['Mic permission', audioDiagnostics.permissionState],
              ['Mic capture', `${audioDiagnostics.availability}${audioDiagnostics.streamActive ? ' · live' : ''}${audioDiagnostics.streamError ? ` · ${audioDiagnostics.streamError}` : ''}`],
              ['Live level', `${Math.round(audioDiagnostics.liveLevel * 100)}%`],
              ['Speech playback', speechState.activeMessageId ? `playing ${speechState.activeMessageId}` : speechState.pendingMessageId ? `starting ${speechState.pendingMessageId}` : 'idle'],
              ['Recent stop marker', summarizeStopReason(events)],
              ['WebRTC state', webrtcDiagnostics.connectionState],
              ['ICE state', webrtcDiagnostics.iceConnectionState],
              ['Signaling state', webrtcDiagnostics.signalingState],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
                <dt>{label}</dt><dd className="truncate pl-3 font-medium text-white">{value}</dd>
              </div>
            ))}
          </dl>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Readiness and warm-up</h3>
            {!readiness ? <p className="text-xs text-slate-500">No readiness snapshot loaded yet.</p> : <div className="space-y-2 text-xs text-slate-300">
              <p className="text-slate-400">Warm-up active: <span className="text-white">{readiness.warmup_active ? 'yes' : 'no'}</span></p>
              {Object.entries(readiness.checks).map(([key, check]) => (
                <div key={key} className="rounded-xl border border-slate-800 px-3 py-2">
                  <div className="flex items-center justify-between gap-3"><span className="font-medium text-white">{check.label}</span><span>{check.status}</span></div>
                  <p className="mt-1 text-slate-400">{check.detail ?? 'No detail recorded.'}</p>
                  <p className="mt-1 text-slate-500">Checked: {formatTime(check.checked_at)}</p>
                </div>
              ))}
            </div>}
          </div>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Local runtime config</h3>
            <pre className="overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">{JSON.stringify(config, null, 2)}</pre>
          </div>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Recent events</h3>
            <div className="space-y-2 text-xs text-slate-300">{events.length === 0 ? <p className="text-slate-500">No backend events received yet.</p> : [...events].slice(-8).reverse().map((event) => <div key={event.id} className="rounded-xl border border-slate-800 px-3 py-2"><div className="flex items-center justify-between gap-3"><span className="font-medium text-white">{event.type}</span><span className="text-slate-500">{formatTime(event.created_at)}</span></div><div className="mt-1 text-slate-500">turn {event.turn_id ?? '—'}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">{JSON.stringify(event.payload, null, 2)}</pre></div>)}</div>
          </div>

          <div className="rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
            <h3 className="mb-3 text-sm font-medium text-white">Recent timings by turn</h3>
            <div className="space-y-2 text-xs text-slate-300">{groupedTimings.length === 0 ? <p className="text-slate-500">No timing records are available yet.</p> : groupedTimings.map((timing) => <div key={timing.id} className="rounded-xl border border-slate-800 px-3 py-2"><div className="flex items-center justify-between gap-3"><span className="font-medium text-white">{timing.phase}</span><span className="text-slate-500">{timing.duration_ms ?? 'pending'} ms</span></div><div className="mt-1 text-slate-500">turn {timing.turn_id ?? 'session'} · started {formatTime(timing.started_at)}</div><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words text-[11px] text-slate-400">{JSON.stringify(timing.meta, null, 2)}</pre></div>)}</div>
          </div>
        </div>
      )}
    </section>
  );
}
