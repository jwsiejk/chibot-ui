import React, { useEffect, useState } from 'react';
import { getLocalGpuValidationReport, getLocalRuntimeReadinessStatus } from '../../../../services/askchappy-api/src/api/server';
import type { LocalRuntimeReadiness } from '../../../../services/askchappy-api/src/api/localRuntimeReadiness';
import type { LocalGpuValidationReport, LocalGpuValidationService, LocalGpuValidationStatus } from '../../../../shared/contracts/gpu';

export type ClientDiagnosticEvent = { id: string; ts: string; event: string };
export type TurnLatencyEntry = {
  id: string;
  ts: string;
  turn_type: 'typed' | 'voice';
  mic_capture_ms: number | null;
  stt_ms: number | null;
  generation_ms: number | null;
  tts_ms: number | null;
  playback_start_ms: number | null;
  total_ms: number | null;
  time_to_text_ready_ms?: number | null;
  time_to_playback_start_ms?: number | null;
  time_to_failure_ms?: number | null;
  failure_stage?: 'stt' | 'assistant_generation' | 'tts' | 'playback' | null;
  tts_skipped_reason?: 'muted' | null;
  playback_skipped_reason?: 'muted' | null;
  assistant_failed?: boolean;
  stt_failed?: boolean;
  tts_failed?: boolean;
};

const STATUS_LABELS: Record<LocalGpuValidationStatus, string> = {
  gpu_confirmed: 'gpu_confirmed',
  cpu_only: 'cpu_only',
  unknown: 'unknown',
  runtime_unreachable: 'runtime_unreachable',
  not_configured: 'not_configured',
  not_applicable: 'not_applicable',
};

const gpuServiceView = (report: LocalGpuValidationReport, service: LocalGpuValidationService) => {
  const match = report.services.find((entry) => entry.service === service);
  if (match) return match;
  return {
    service,
    status: 'unknown' as const,
    reason: 'Service validation entry not available in this report.',
    suggested_commands: [],
  };
};

export const AdminRuntimeConsoleModal = ({ isOpen, onClose, browserMicStatus, diagnostics, turnLatency }: { isOpen: boolean; onClose: () => void; browserMicStatus: string; diagnostics: ClientDiagnosticEvent[]; turnLatency: TurnLatencyEntry[] }) => {
  const [readiness, setReadiness] = useState<LocalRuntimeReadiness | null>(null);
  const [gpu, setGpu] = useState<LocalGpuValidationReport | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    void getLocalRuntimeReadinessStatus().then(setReadiness);
    void getLocalGpuValidationReport().then(setGpu);
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="admin-runtime-modal-backdrop" role="presentation">
      <section className="card admin-runtime-modal" role="dialog" aria-label="Admin Runtime Console">
        <header className="admin-runtime-modal-header">
          <h2>Admin Runtime Console</h2>
          <button className="meeting-btn" type="button" onClick={onClose}>Close</button>
        </header>

        <section>
          <h3>Local Runtime Readiness</h3>
          {!readiness ? <p>Loading runtime readiness…</p> : (
            <ul>
              <li>Ollama: {readiness.ollama.status} — {readiness.ollama.reason}</li>
              <li>Kokoro TTS: {readiness.kokoro_tts.status} — {readiness.kokoro_tts.reason}</li>
              <li>faster-whisper STT: {readiness.faster_whisper_stt.status} — {readiness.faster_whisper_stt.reason}</li>
              <li>Standard voice: {readiness.standard_voice.status} — {readiness.standard_voice.reason}</li>
              <li>Cloned voice: {readiness.cloned_voice.status} — {readiness.cloned_voice.reason}</li>
              <li>Browser mic: {browserMicStatus}</li>
            </ul>
          )}
        </section>

        <section>
          <h3>GPU Validation</h3>
          {!gpu ? <p>Loading GPU validation…</p> : (
            <ul>
              <li>Ollama GPU: {STATUS_LABELS[gpuServiceView(gpu, 'ollama').status]} — {gpuServiceView(gpu, 'ollama').reason}</li>
              <li>faster-whisper GPU: {STATUS_LABELS[gpuServiceView(gpu, 'faster_whisper').status]} — {gpuServiceView(gpu, 'faster_whisper').reason}</li>
              <li>Kokoro provider/GPU: {STATUS_LABELS[gpuServiceView(gpu, 'kokoro_onnx').status]} — {gpuServiceView(gpu, 'kokoro_onnx').reason}</li>
              {gpu.manual_guidance.map((guidance) => <li key={guidance}>Manual guidance: <code>{guidance}</code></li>)}
            </ul>
          )}
        </section>

        <section>
          <h3>Service Endpoints</h3>
          <ul>
            <li>Ollama: <code>http://127.0.0.1:11434</code></li>
            <li>Kokoro: <code>http://127.0.0.1:8880</code></li>
            <li>faster-whisper: <code>http://127.0.0.1:8890</code></li>
            <li>AskChappy app: <code>http://127.0.0.1:4173/chappy</code></li>
          </ul>
        </section>

        <section>
          <h3>Troubleshooting</h3>
          <ul>
            <li>If Ollama unreachable: check <code>ollama serve</code> and <code>/api/tags</code>.</li>
            <li>If Kokoro unreachable: run <code>.\scripts\start-kokoro-tts.ps1</code>.</li>
            <li>If faster-whisper unreachable: run <code>.\scripts\start-faster-whisper-stt.ps1</code>.</li>
            <li>If GPU unknown: run <code>nvidia-smi -l 1</code>.</li>
            <li>If TTS unavailable: transcript text still works.</li>
            <li>If STT no speech: no transcript message is created.</li>
          </ul>
        </section>


        <section>
          <h3>Turn Latency</h3>
          {turnLatency.length === 0 ? <p>No turn latency metrics yet.</p> : (
            <>
              <p>All latency metrics are local-only in-memory diagnostics. “Time to Chappy speaking” means time to playback start (not playback end).</p>
              <h4>Latest turn</h4>
              <ul>
                <li>Type: {turnLatency[0].turn_type}</li>
                <li>Failure stage: {turnLatency[0].failure_stage ?? 'none'}</li>
                <li>STT: {turnLatency[0].stt_ms ?? 'n/a'} ms</li>
                <li>Assistant generation: {turnLatency[0].generation_ms ?? 'n/a'} ms</li>
                <li>TTS synthesis: {turnLatency[0].tts_ms ?? 'n/a'} ms</li>
                <li>Time to assistant text ready: {turnLatency[0].time_to_text_ready_ms ?? 'n/a'} ms</li>
                <li>Time to Chappy speaking: {turnLatency[0].time_to_playback_start_ms ?? 'n/a'} ms</li>
                {turnLatency[0].tts_skipped_reason ? <li>TTS skipped: {turnLatency[0].tts_skipped_reason}</li> : null}
                {turnLatency[0].playback_skipped_reason ? <li>Playback skipped: {turnLatency[0].playback_skipped_reason}</li> : null}
                {turnLatency[0].stt_failed ? <li>STT failed</li> : null}
                {turnLatency[0].assistant_failed ? <li>Assistant generation failed</li> : null}
                {turnLatency[0].tts_failed && turnLatency[0].failure_stage === 'tts' ? <li>TTS failed</li> : null}
                {turnLatency[0].tts_failed && turnLatency[0].failure_stage === 'playback' ? <li>Playback failed</li> : null}
              </ul>
              <h4>Last 5 turns</h4>
              <ul>
                {turnLatency.slice(0, 5).map((entry) => <li key={entry.id}>{entry.ts} — {entry.turn_type} — Failure {entry.failure_stage ?? 'none'} — STT {entry.stt_ms ?? 'n/a'}ms — Assistant {entry.generation_ms ?? 'n/a'}ms — TTS {entry.tts_ms ?? 'n/a'}ms — Text ready {entry.time_to_text_ready_ms ?? 'n/a'}ms — Chappy speaking {entry.time_to_playback_start_ms ?? 'n/a'}ms{entry.tts_skipped_reason ? ` — TTS skipped: ${entry.tts_skipped_reason}` : ''}{entry.playback_skipped_reason ? ` — Playback skipped: ${entry.playback_skipped_reason}` : ''}{entry.stt_failed ? ' — STT failed' : ''}{entry.assistant_failed ? ' — Assistant failed' : ''}{entry.tts_failed && entry.failure_stage === 'tts' ? ' — TTS failed' : ''}{entry.tts_failed && entry.failure_stage === 'playback' ? ' — Playback failed' : ''}</li>)}
              </ul>
            </>
          )}
        </section>

        <section>
          <h3>Diagnostics</h3>
          {diagnostics.length === 0 ? <p>No client diagnostic events yet.</p> : (
            <ul>
              {diagnostics.map((event) => <li key={event.id}>{event.ts} — {event.event}</li>)}
            </ul>
          )}
        </section>
      </section>
    </div>
  );
};
