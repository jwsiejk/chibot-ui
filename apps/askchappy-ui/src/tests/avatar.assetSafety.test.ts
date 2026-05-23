import { describe, expect, it } from 'vitest';
import { execSync } from 'node:child_process';

describe('phase 11 avatar/voice private asset safety', () => {
  it('does not commit private voice/avatar likeness or model artifact files', () => {
    const trackedFiles = execSync('git ls-files', { encoding: 'utf8' })
      .split('\n')
      .map((line) => line.trim().toLowerCase())
      .filter(Boolean);

    const forbiddenPattern = /\.(wav|mp3|m4a|ogg|flac|webm|pt|ckpt|onnx|npy|npz|pkl|emb|embedding|glb|gltf|fbx|obj)$/;
    const forbiddenAssetPaths = trackedFiles.filter(
      (file) => forbiddenPattern.test(file) || file.includes('/assets/avatar') || file.includes('/assets/voice'),
    );

    expect(forbiddenAssetPaths).toEqual([]);
  });
});
