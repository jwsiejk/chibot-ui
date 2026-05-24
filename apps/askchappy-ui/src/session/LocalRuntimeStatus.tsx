import React, { useEffect, useState } from 'react';
import { getLocalRuntimeReadinessStatus } from '../../../../services/askchappy-api/src/api/server';
import type { LocalRuntimeReadiness } from '../../../../services/askchappy-api/src/api/localRuntimeReadiness';

type MicStatus = 'checking' | 'available' | 'permission_denied' | 'unavailable';

const getMicReason = (mic: MicStatus): string => {
  if (mic === 'permission_denied') return 'Microphone permission is denied in this browser.';
  if (mic === 'unavailable') return 'Browser microphone APIs are unavailable in this environment.';
  if (mic === 'available') return 'Browser microphone is available for local STT capture.';
  return 'Checking browser microphone capability.';
};

export const LocalRuntimeStatus = ({ compact = false }: { compact?: boolean }) => {
  const [status, setStatus] = useState<LocalRuntimeReadiness | null>(null);
  const [mic, setMic] = useState<MicStatus>('checking');

  useEffect(() => {
    void getLocalRuntimeReadinessStatus().then(setStatus);
    if (!navigator.mediaDevices?.getUserMedia) {
      setMic('unavailable');
      return;
    }
    const permissions = (navigator as Navigator & { permissions?: { query: (x: { name: 'microphone' }) => Promise<{ state: string }> } }).permissions;
    if (!permissions?.query) {
      setMic('available');
      return;
    }
    void permissions.query({ name: 'microphone' }).then((p) => {
      setMic(p.state === 'denied' ? 'permission_denied' : 'available');
    }).catch(() => setMic('available'));
  }, []);

  if (!status && compact) {
    return (
      <p className="meeting-control-btn utility-control toolbar-runtime-loading" aria-label="runtime status">
        <span className="meeting-control-icon" aria-hidden="true">▶</span>
        <span className="meeting-control-label">Runtime</span>
      </p>
    );
  }

  if (!status) return <p className="card panel">Local runtime status: checking…</p>;

  if (compact) {
    return (
      <details className="toolbar-runtime runtime-details">
        <summary className="meeting-control-btn utility-control">
          <span className="meeting-control-icon" aria-hidden="true">▶</span>
          <span className="meeting-control-label">Runtime</span>
        </summary>
        <ul>
        <li>Ollama: {status.ollama.status} — {status.ollama.reason}</li>
        <li>Kokoro TTS: {status.kokoro_tts.status} — {status.kokoro_tts.reason}</li>
        <li>faster-whisper STT: {status.faster_whisper_stt.status} — {status.faster_whisper_stt.reason}</li>
        <li>Browser mic: {mic} — {getMicReason(mic)}</li>
        <li>Standard voice: {status.standard_voice.status} — {status.standard_voice.reason}</li>
        <li>Cloned voice: {status.cloned_voice.status} — {status.cloned_voice.reason}</li>
        </ul>
      </details>
    );
  }

  return (
    <details className="card panel runtime-details">
      <summary>Runtime readiness details</summary>
      <ul>
        <li>Ollama: {status.ollama.status} — {status.ollama.reason}</li>
        <li>Kokoro TTS: {status.kokoro_tts.status} — {status.kokoro_tts.reason}</li>
        <li>faster-whisper STT: {status.faster_whisper_stt.status} — {status.faster_whisper_stt.reason}</li>
        <li>Browser mic: {mic} — {getMicReason(mic)}</li>
        <li>Standard voice: {status.standard_voice.status} — {status.standard_voice.reason}</li>
        <li>Cloned voice: {status.cloned_voice.status} — {status.cloned_voice.reason}</li>
      </ul>
    </details>
  );
};
