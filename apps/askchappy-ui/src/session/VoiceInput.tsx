import React, { useEffect, useRef, useState } from 'react';

export type VoiceInputStatus = 'checking_mic' | 'mic_unavailable' | 'permission_denied' | 'ready_to_record' | 'recording' | 'transcribing';

export const VoiceInput = ({ onStart, onStop, onTranscribe, onError, disabled }: { onStart: () => void; onStop: () => void; onTranscribe: (blob: Blob) => Promise<void>; onError: (message: string) => void; disabled?: boolean; }) => {
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
  } catch { setStatus('permission_denied'); onError('Microphone permission denied or unavailable.'); } };

  const stopRecording = () => { onStop(); recorder.current?.stop(); stream.current?.getTracks().forEach((track) => track.stop()); };

  return (
    <section className="card panel" aria-label="voice input panel">
      <p>Microphone status: {status.replaceAll('_', ' ')}</p>
      <div className="voice-row">
        {status === 'mic_unavailable' || status === 'permission_denied' ? null : status === 'recording' ? (
          <button className="btn secondary" type="button" onClick={stopRecording} disabled={disabled}>Stop recording</button>
        ) : (
          <button className="btn secondary" type="button" onClick={startRecording} disabled={disabled || status === 'checking_mic' || status === 'transcribing'}>Start speaking</button>
        )}
      </div>
    </section>
  );
};
