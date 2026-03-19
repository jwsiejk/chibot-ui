import type { AudioDiagnosticsSnapshot, AudioInputDevice } from './types';
import type { WebRtcDiagnosticsSnapshot } from '../webrtc/types';

function meterWidth(level: number): string {
  return `${Math.round(level * 100)}%`;
}

export function MicSetupPanel({
  devices,
  selectedDeviceId,
  diagnostics,
  webrtcDiagnostics,
  audioUnlocked,
  onUnlock,
  onRefresh,
  onStart,
  onConnectWebRtc,
  onSelectDevice,
}: {
  devices: AudioInputDevice[];
  selectedDeviceId: string | null;
  diagnostics: AudioDiagnosticsSnapshot;
  webrtcDiagnostics: WebRtcDiagnosticsSnapshot;
  audioUnlocked: boolean;
  onUnlock: () => Promise<void>;
  onRefresh: () => Promise<unknown>;
  onStart: () => Promise<void>;
  onConnectWebRtc: () => Promise<void>;
  onSelectDevice: (deviceId: string) => Promise<void>;
}) {
  return (
    <section className="rounded-[2rem] border border-slate-800 bg-panel/80 p-5 shadow-panel backdrop-blur">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-200/80">Audio preflight</p>
          <h2 className="text-lg font-semibold text-white">Microphone setup</h2>
        </div>
        <button
          type="button"
          onClick={() => void onRefresh()}
          className="rounded-full border border-slate-700 bg-slate-950/60 px-3 py-1 text-xs font-medium text-slate-200 transition hover:border-cyan-400/40 hover:text-white"
        >
          Refresh devices
        </button>
      </header>

      <div className="space-y-4 text-sm text-slate-300">
        <p className="rounded-2xl border border-slate-800 px-4 py-3 text-slate-300">
          This phase verifies microphone/device readiness plus the WebRTC transport foundation only. Typed chat remains the source of truth, and voice turns are still out of scope.
        </p>

        <label className="block space-y-2">
          <span className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Input device</span>
          <select
            value={selectedDeviceId ?? ''}
            onChange={(event) => void onSelectDevice(event.target.value)}
            className="w-full rounded-2xl border border-slate-800 bg-slate-950/70 px-4 py-3 text-sm text-white outline-none"
            disabled={devices.length === 0}
          >
            {devices.length === 0 ? <option value="">No microphones detected</option> : devices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>{device.label}{device.isDefault ? ' (default)' : ''}</option>
            ))}
          </select>
        </label>

        <div className="grid gap-3 sm:grid-cols-3">
          <button
            type="button"
            onClick={() => void onUnlock()}
            className="rounded-[1.25rem] border border-slate-700 bg-slate-950/60 px-4 py-3 font-medium text-white transition hover:border-cyan-400/40"
          >
            {audioUnlocked ? 'Audio unlocked' : 'Unlock browser audio'}
          </button>
          <button
            type="button"
            onClick={() => void onStart()}
            className="rounded-[1.25rem] bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300"
          >
            Start mic test
          </button>
          <button
            type="button"
            onClick={() => void onConnectWebRtc()}
            className="rounded-[1.25rem] border border-cyan-400/40 bg-slate-950/60 px-4 py-3 font-medium text-cyan-100 transition hover:border-cyan-300 hover:text-white"
          >
            Connect WebRTC foundation
          </button>
        </div>

        <div className="space-y-2 rounded-[1.5rem] border border-slate-900 bg-slate-950/60 p-4">
          <div className="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.2em] text-slate-400">
            <span>Live input level</span>
            <span>{Math.round(diagnostics.liveLevel * 100)}%</span>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-800">
            <div className="h-full rounded-full bg-cyan-400 transition-[width]" style={{ width: meterWidth(diagnostics.liveLevel) }} />
          </div>
        </div>

        <dl className="grid gap-3 text-sm">
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
            <dt>Permission</dt>
            <dd className="font-medium text-white">{diagnostics.permissionState}</dd>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
            <dt>Availability</dt>
            <dd className="font-medium text-white">{diagnostics.availability}</dd>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
            <dt>WebRTC</dt>
            <dd className="font-medium text-white">{webrtcDiagnostics.connectionState}</dd>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
            <dt>ICE</dt>
            <dd className="font-medium text-white">{webrtcDiagnostics.iceConnectionState}</dd>
          </div>
          <div className="flex items-center justify-between rounded-2xl border border-slate-800 px-4 py-3">
            <dt>Signaling</dt>
            <dd className="font-medium text-white">{webrtcDiagnostics.signalingState}</dd>
          </div>
        </dl>

        {diagnostics.streamError && (
          <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            Audio setup issue: {diagnostics.streamError}
          </div>
        )}
        {webrtcDiagnostics.lastError && (
          <div className="rounded-2xl border border-slate-700 bg-slate-950/60 px-4 py-3 text-xs text-slate-300">
            WebRTC detail: {webrtcDiagnostics.lastError}
          </div>
        )}
      </div>
    </section>
  );
}
