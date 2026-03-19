import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { buildMicrophoneConstraints } from '../.test-dist/audio/constraints.js';
import { loadPreferredMicDeviceId, savePreferredMicDeviceId } from '../.test-dist/audio/storage.js';

function makeStorage() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, value);
    },
    removeItem(key) {
      data.delete(key);
    },
  };
}

describe('audio foundation helpers', () => {
  it('persists and clears the preferred microphone device id', () => {
    const storage = makeStorage();

    savePreferredMicDeviceId('mic-7', storage);
    assert.equal(loadPreferredMicDeviceId(storage), 'mic-7');

    savePreferredMicDeviceId(null, storage);
    assert.equal(loadPreferredMicDeviceId(storage), null);
  });

  it('builds conversational microphone constraints with best-effort processing flags', () => {
    assert.deepEqual(buildMicrophoneConstraints('mic-9'), {
      audio: {
        deviceId: { exact: 'mic-9' },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  });
});
