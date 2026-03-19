const STORAGE_KEY = 'askchip-ui.preferred-mic-device-id';

export function loadPreferredMicDeviceId(storage: Pick<Storage, 'getItem'> = window.localStorage): string | null {
  return storage.getItem(STORAGE_KEY);
}

export function savePreferredMicDeviceId(deviceId: string | null, storage: Pick<Storage, 'setItem' | 'removeItem'> = window.localStorage): void {
  if (deviceId) {
    storage.setItem(STORAGE_KEY, deviceId);
    return;
  }
  storage.removeItem(STORAGE_KEY);
}

export { STORAGE_KEY as PREFERRED_MIC_STORAGE_KEY };
