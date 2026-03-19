export type MicPermissionState = PermissionState | 'unsupported' | 'unknown';
export type MicAvailabilityState = 'idle' | 'ready' | 'blocked' | 'unavailable' | 'lost-device' | 'error';

export interface AudioInputDevice {
  deviceId: string;
  label: string;
  groupId: string;
  isDefault: boolean;
}

export interface AudioDiagnosticsSnapshot {
  selectedDeviceId: string | null;
  selectedDeviceLabel: string | null;
  permissionState: MicPermissionState;
  availability: MicAvailabilityState;
  liveLevel: number;
  deviceChangeCount: number;
  lastDeviceChangeAt: string | null;
  streamActive: boolean;
  streamError: string | null;
}
