import React, { useRef, useState } from 'react';

export const VoiceInput = ({
  onStart,
  onStop,
  onTranscribe,
  onError,
  disabled,
}: {
  onStart: () => void;
  onStop: () => void;
  onTranscribe: (blob: Blob) => Promise<void>;
  onError: (message: string) => void;
  disabled?: boolean;
}) => {
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<BlobPart[]>([]);
  const [isRecording, setIsRecording] = useState(false);

  const startRecording = async () => {
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = media;
      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      recorder.current = new MediaRecorder(media, mimeType ? { mimeType } : undefined);
      chunks.current = [];
      recorder.current.ondataavailable = (event) => chunks.current.push(event.data);
      recorder.current.onstop = async () => {
        const blob = new Blob(chunks.current, { type: mimeType || 'audio/webm' });
        await onTranscribe(blob);
      };
      recorder.current.start();
      onStart();
      setIsRecording(true);
    } catch {
      onError('Microphone permission denied or unavailable.');
    }
  };

  const stopRecording = () => {
    onStop();
    recorder.current?.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    setIsRecording(false);
  };

  return isRecording ? (
    <button type="button" onClick={stopRecording} disabled={disabled}>Stop recording</button>
  ) : (
    <button type="button" onClick={startRecording} disabled={disabled}>Start speaking</button>
  );
};
