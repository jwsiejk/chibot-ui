import React, { useEffect, useState } from 'react';
import { getLocalGpuValidationReport, getLocalRuntimeReadinessStatus } from '../../../../services/askchappy-api/src/api/server';
import type { LocalRuntimeReadiness } from '../../../../services/askchappy-api/src/api/localRuntimeReadiness';
import type { LocalGpuValidationStatus } from '../../../../services/askchappy-api/src/api/localGpuValidation';

export type ClientDiagnosticEvent = { id: string; ts: string; event: string };

export const AdminRuntimeConsoleModal = ({ isOpen, onClose, browserMicStatus, diagnostics }: { isOpen: boolean; onClose: () => void; browserMicStatus: string; diagnostics: ClientDiagnosticEvent[] }) => {
  const [readiness, setReadiness] = useState<LocalRuntimeReadiness | null>(null);
  const [gpu, setGpu] = useState<LocalGpuValidationStatus | null>(null);

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
              <li>Ollama GPU: {gpu.ollama.status} — {gpu.ollama.reason}</li>
              <li>faster-whisper GPU: {gpu.faster_whisper.status} — {gpu.faster_whisper.reason}</li>
              <li>Kokoro provider/GPU: {gpu.kokoro.status} — {gpu.kokoro.reason}</li>
              <li>Suggested validation: <code>nvidia-smi -l 1</code></li>
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
