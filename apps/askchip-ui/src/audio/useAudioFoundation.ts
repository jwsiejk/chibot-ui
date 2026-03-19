import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { acquireMicrophone, enumerateAudioInputs, queryMicrophonePermission, stopStream, supportsMediaDevices } from './mediaDevices';
import { LiveLevelMeter, normalizeLiveLevel } from './levelMeter';
import { loadPreferredMicDeviceId, savePreferredMicDeviceId } from './storage';
import type { AudioDiagnosticsSnapshot, AudioInputDevice, MicAvailabilityState } from './types';
import { WebRtcManager } from '../webrtc/WebRtcManager';
import type { WebRtcDiagnosticsSnapshot } from '../webrtc/types';

const DEFAULT_WEBRTC: WebRtcDiagnosticsSnapshot = {
  sessionId: null,
  connectionState: 'idle',
  iceConnectionState: 'new',
  signalingState: 'stable',
  lastError: null,
};

export function useAudioFoundation(sessionId: string | null) {
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(() => loadPreferredMicDeviceId());
  const [permissionState, setPermissionState] = useState<AudioDiagnosticsSnapshot['permissionState']>('unknown');
  const [availability, setAvailability] = useState<MicAvailabilityState>('idle');
  const [liveLevel, setLiveLevel] = useState(0);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamActive, setStreamActive] = useState(false);
  const [deviceChangeCount, setDeviceChangeCount] = useState(0);
  const [lastDeviceChangeAt, setLastDeviceChangeAt] = useState<string | null>(null);
  const [audioUnlocked, setAudioUnlocked] = useState(false);
  const [webrtcDiagnostics, setWebrtcDiagnostics] = useState<WebRtcDiagnosticsSnapshot>(DEFAULT_WEBRTC);
  const meterRef = useRef(new LiveLevelMeter());
  const streamRef = useRef<MediaStream | null>(null);
  const webRtcRef = useRef(new WebRtcManager(setWebrtcDiagnostics));

  const refreshDevices = useCallback(async () => {
    const nextDevices = await enumerateAudioInputs();
    setDevices(nextDevices);
    if (nextDevices.length === 0) {
      setAvailability('unavailable');
      return nextDevices;
    }
    setSelectedDeviceId((current) => {
      const resolved = current && nextDevices.some((device) => device.deviceId === current)
        ? current
        : nextDevices[0].deviceId;
      savePreferredMicDeviceId(resolved);
      return resolved;
    });
    return nextDevices;
  }, []);

  const releaseStream = useCallback(() => {
    meterRef.current.stop();
    stopStream(streamRef.current);
    streamRef.current = null;
    setStreamActive(false);
    setLiveLevel(0);
    webRtcRef.current.disconnect();
  }, []);

  const unlockAudio = useCallback(async () => {
    if (audioUnlocked) {
      return;
    }
    const AudioContextCtor = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) {
      setAudioUnlocked(true);
      return;
    }
    const context = new AudioContextCtor();
    if (context.state === 'suspended') {
      await context.resume();
    }
    await context.close();
    setAudioUnlocked(true);
  }, [audioUnlocked]);

  const startMicrophone = useCallback(async (deviceIdOverride?: string | null) => {
    if (!supportsMediaDevices()) {
      setPermissionState('unsupported');
      setAvailability('unavailable');
      setStreamError('This browser does not support microphone capture APIs.');
      return;
    }

    const deviceId = deviceIdOverride ?? selectedDeviceId;
    releaseStream();
    setStreamError(null);
    const nextPermission = await queryMicrophonePermission();
    setPermissionState(nextPermission);

    try {
      await unlockAudio();
      const stream = await acquireMicrophone(deviceId);
      streamRef.current = stream;
      setStreamActive(true);
      setAvailability('ready');
      setPermissionState('granted');
      await meterRef.current.start(stream, (value) => setLiveLevel(normalizeLiveLevel(value)));
      await refreshDevices();
      await webRtcRef.current.connect(stream, sessionId);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to access microphone.';
      setStreamError(message);
      if (error instanceof DOMException && error.name === 'NotAllowedError') {
        setAvailability('blocked');
        setPermissionState('denied');
      } else if (error instanceof DOMException && (error.name === 'NotFoundError' || error.name === 'OverconstrainedError')) {
        setAvailability('unavailable');
      } else {
        setAvailability('error');
      }
    }
  }, [refreshDevices, releaseStream, selectedDeviceId, sessionId, unlockAudio]);

  const selectDevice = useCallback(async (deviceId: string) => {
    setSelectedDeviceId(deviceId);
    savePreferredMicDeviceId(deviceId);
    if (streamActive) {
      await startMicrophone(deviceId);
    }
  }, [startMicrophone, streamActive]);

  useEffect(() => {
    void queryMicrophonePermission().then(setPermissionState);
    void refreshDevices();
  }, [refreshDevices]);

  useEffect(() => {
    if (!navigator.mediaDevices?.addEventListener) {
      return undefined;
    }
    const handleDeviceChange = () => {
      setDeviceChangeCount((count) => count + 1);
      setLastDeviceChangeAt(new Date().toISOString());
      void refreshDevices().then((nextDevices) => {
        if (selectedDeviceId && !nextDevices.some((device) => device.deviceId === selectedDeviceId)) {
          setAvailability('lost-device');
          const fallbackId = nextDevices[0]?.deviceId ?? null;
          setSelectedDeviceId(fallbackId);
          savePreferredMicDeviceId(fallbackId);
          if (fallbackId) {
            void startMicrophone(fallbackId);
          } else {
            releaseStream();
          }
        }
      });
    };
    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange);
    return () => navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange);
  }, [refreshDevices, releaseStream, selectedDeviceId, startMicrophone]);

  useEffect(() => () => releaseStream(), [releaseStream]);

  const diagnostics = useMemo<AudioDiagnosticsSnapshot>(() => {
    const selectedDevice = devices.find((device) => device.deviceId === selectedDeviceId) ?? null;
    return {
      selectedDeviceId,
      selectedDeviceLabel: selectedDevice?.label ?? null,
      permissionState,
      availability,
      liveLevel,
      deviceChangeCount,
      lastDeviceChangeAt,
      streamActive,
      streamError,
    };
  }, [availability, deviceChangeCount, devices, lastDeviceChangeAt, liveLevel, permissionState, selectedDeviceId, streamActive, streamError]);

  return {
    devices,
    selectedDeviceId,
    diagnostics,
    webrtcDiagnostics,
    audioUnlocked,
    actions: {
      unlockAudio,
      refreshDevices,
      startMicrophone,
      selectDevice,
      releaseStream,
    },
  };
}
