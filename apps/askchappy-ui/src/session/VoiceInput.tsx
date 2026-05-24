import React, { useEffect, useRef, useState } from 'react';

export type VoiceInputStatus = 'checking_mic' | 'mic_unavailable' | 'permission_denied' | 'ready_to_record' | 'recording' | 'transcribing';

export const VoiceInput = ({ onStart, onStop, onTranscribe, onError, disabled, compact = false }: { onStart: () => void; onStop: () => void; onTranscribe: (blob: Blob) => Promise<void>; onError: (message: string) => void; disabled?: boolean; compact?: boolean; }) => {
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<BlobPart[]>([]);
  const [status, setStatus] = useState<VoiceInputStatus>('checking_mic');

  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') { setStatus('mic_unavailable'); return; }
    setStatus('ready_to_record');
  }, []);

  const startRecording = async () => { try {
    const media = await navigator.mediaDevices.getUserMedia({ audio: true }); stream.current = media;
    const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
    recorder.current = new MediaRecorder(media, mimeType ? { mimeType } : undefined); chunks.current = [];
    recorder.current.ondataavailable = (event) => chunks.current.push(event.data);
    recorder.current.onstop = async () => { setStatus('transcribing'); const blob = new Blob(chunks.current, { type: mimeType || 'audio/webm' }); await onTranscribe(blob); setStatus('ready_to_record'); };
    recorder.current.start(); onStart(); setStatus('recording');
  } catch { setStatus('permission_denied'); onError('Permission denied'); } };

  const stopRecording = () => { onStop(); recorder.current?.stop(); stream.current?.getTracks().forEach((track) => track.stop()); };

  const unavailable = disabled || status === 'checking_mic' || status === 'transcribing' || status === 'mic_unavailable' || status === 'permission_denied';

  if (compact) {
    return (
      <div className="meeting-control meeting-mic" aria-label="voice input panel">
        <button className="meeting-btn" type="button" onClick={status === 'recording' ? stopRecording : startRecording} disabled={unavailable} aria-label={`Mic ${status.replaceAll('_', ' ')}`}>
          <span aria-hidden="true">🎙️</span> Mic
        </button>
      </div>
    );
  }

  return (
    <section className="card panel" aria-label="voice input panel">
      <p>Microphone status: {status.replaceAll('_', ' ')}</p>
      <div className="voice-row">
        {status === 'recording' ? ( <button className="btn secondary" type="button" onClick={stopRecording} disabled={unavailable}>Stop speaking</button>) : (<button className="btn secondary" type="button" onClick={startRecording} disabled={unavailable}>Start speaking</button>)}
      </div>
    </section>
  );
};
