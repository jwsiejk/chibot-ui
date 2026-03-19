import type { AudioInputDevice, MicPermissionState } from './types';
import { buildMicrophoneConstraints } from './constraints';

export function supportsMediaDevices(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
}

export async function queryMicrophonePermission(): Promise<MicPermissionState> {
  if (typeof navigator === 'undefined' || !('permissions' in navigator) || !navigator.permissions?.query) {
    return 'unknown';
  }
  try {
    const status = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    return status.state;
  } catch {
    return 'unknown';
  }
}

export async function enumerateAudioInputs(): Promise<AudioInputDevice[]> {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return [];
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `Microphone ${index + 1}`,
      groupId: device.groupId,
      isDefault: device.deviceId === 'default' || index === 0,
    }));
}

export async function acquireMicrophone(deviceId: string | null): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia(buildMicrophoneConstraints(deviceId));
}

export function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}
