import React, { useEffect, useState } from 'react';
import { getLocalRuntimeReadinessStatus } from '../../../../services/askchappy-api/src/api/server';
import type { LocalRuntimeReadiness } from '../../../../services/askchappy-api/src/api/localRuntimeReadiness';

type MicStatus = 'checking' | 'available' | 'permission_denied' | 'unavailable';

export const LocalRuntimeStatus = () => {
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

  if (!status) return <p>Local runtime status: checking…</p>;

  return (
    <section>
      <h2>Local runtime readiness</h2>
      <ul>
        <li>Ollama: {status.ollama.status}</li>
        <li>Kokoro TTS: {status.kokoro_tts.status}</li>
        <li>faster-whisper STT: {status.faster_whisper_stt.status}</li>
        <li>Browser mic: {mic}</li>
        <li>Standard voice: selected/default</li>
        <li>Cloned voice: optional/gated, not required</li>
      </ul>
    </section>
  );
};
