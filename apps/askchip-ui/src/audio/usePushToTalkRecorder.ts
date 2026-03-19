import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { acquireMicrophone, stopStream, supportsMediaDevices } from './mediaDevices';

export interface RecordedVoiceTurn {
  blob: Blob;
  durationMs: number;
  mimeType: string;
}

export interface PushToTalkStatus {
  phase: 'idle' | 'listening' | 'transcribing';
  startedAt: number | null;
  error: string | null;
}

const DEFAULT_MIME_TYPE = 'audio/webm';

function resolveMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return DEFAULT_MIME_TYPE;
  }
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((candidate) => MediaRecorder.isTypeSupported(candidate)) ?? DEFAULT_MIME_TYPE;
}

export function usePushToTalkRecorder(selectedDeviceId: string | null) {
  const [status, setStatus] = useState<PushToTalkStatus>({ phase: 'idle', startedAt: null, error: null });
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number | null>(null);

  const reset = useCallback(() => {
    recorderRef.current = null;
    chunksRef.current = [];
    stopStream(streamRef.current);
    streamRef.current = null;
  }, []);

  const beginCapture = useCallback(async () => {
    if (!supportsMediaDevices()) {
      setStatus({ phase: 'idle', startedAt: null, error: 'This browser does not support microphone capture APIs.' });
      throw new Error('This browser does not support microphone capture APIs.');
    }
    if (typeof MediaRecorder === 'undefined') {
      setStatus({ phase: 'idle', startedAt: null, error: 'This browser does not support MediaRecorder for push-to-talk capture.' });
      throw new Error('This browser does not support MediaRecorder for push-to-talk capture.');
    }

    const stream = await acquireMicrophone(selectedDeviceId);
    const recorder = new MediaRecorder(stream, { mimeType: resolveMimeType() });
    streamRef.current = stream;
    recorderRef.current = recorder;
    chunksRef.current = [];
    startedAtRef.current = Date.now();
    setStatus({ phase: 'listening', startedAt: startedAtRef.current, error: null });

    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size > 0) {
        chunksRef.current.push(event.data);
      }
    });

    recorder.start();
  }, [selectedDeviceId]);

  const finishCapture = useCallback(async (): Promise<RecordedVoiceTurn> => {
    const recorder = recorderRef.current;
    const startedAt = startedAtRef.current;
    if (!recorder || !startedAt) {
      throw new Error('Push-to-talk capture is not active.');
    }

    setStatus((current) => ({ ...current, phase: 'transcribing' }));

    const result = await new Promise<RecordedVoiceTurn>((resolve, reject) => {
      recorder.addEventListener('stop', () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || DEFAULT_MIME_TYPE });
        resolve({
          blob,
          durationMs: Math.max(0, Date.now() - startedAt),
          mimeType: recorder.mimeType || DEFAULT_MIME_TYPE,
        });
      }, { once: true });
      recorder.addEventListener('error', () => reject(new Error('Push-to-talk recording failed.')), { once: true });
      recorder.stop();
    });

    reset();
    startedAtRef.current = null;
    return result;
  }, [reset]);

  const cancelCapture = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    reset();
    startedAtRef.current = null;
    setStatus({ phase: 'idle', startedAt: null, error: null });
  }, [reset]);

  const markComplete = useCallback(() => {
    startedAtRef.current = null;
    setStatus({ phase: 'idle', startedAt: null, error: null });
  }, []);

  useEffect(() => () => {
    cancelCapture();
  }, [cancelCapture]);

  const active = useMemo(() => status.phase === 'listening' || status.phase === 'transcribing', [status.phase]);

  return {
    status,
    active,
    actions: {
      beginCapture,
      finishCapture,
      cancelCapture,
      markComplete,
      getStartedAt: () => startedAtRef.current,
    },
  };
}
