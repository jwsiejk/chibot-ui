import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.cwd();
const scripts = [
  'scripts/check-local-runtime.ps1',
  'scripts/start-kokoro-tts.ps1',
  'scripts/start-faster-whisper-stt.ps1',
  'scripts/start-local-runtime.ps1',
];

describe('phase 21B windows local runtime startup scripts', () => {
  it('includes required startup/check scripts', () => {
    for (const file of scripts) expect(existsSync(resolve(root, file))).toBe(true);
  });

  it('references .env.local and avoids cloud/openai providers', () => {
    const combined = scripts.map((f) => readFileSync(resolve(root, f), 'utf8')).join('\n');
    expect(combined).toContain('.env.local');
    expect(combined.toLowerCase()).not.toContain('openai');
    expect(combined.toLowerCase()).not.toContain('api.openai.com');
    expect(combined.toLowerCase()).not.toContain('azure');
    expect(combined.toLowerCase()).not.toContain('anthropic');
  });

  it('keeps safe placeholder env contracts and gitignore behavior', () => {
    const envExample = readFileSync(resolve(root, '.env.example'), 'utf8');
    expect(envExample).toContain('KOKORO_TTS_ASSET_DIR=C:\\AskChipAssets\\kokoro');
    expect(envExample).toContain('KOKORO_TTS_RUN_COMMAND=');
    expect(envExample).toContain('FASTER_WHISPER_RUN_COMMAND=');
    expect(envExample.toLowerCase()).not.toContain('openai');

    const gitignore = readFileSync(resolve(root, '.gitignore'), 'utf8');
    expect(gitignore).toContain('.env.local');
  });

  it('documents startup scripts in operator docs', () => {
    const docs = [
      'README.md',
      'docs/LOCAL_FIRST_RUN_GUIDE.md',
      'docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md',
      'docs/CURRENT_IMPLEMENTATION_STATUS.md',
      'docs/LOCAL_FIRST_RELEASE_CHECKLIST.md',
    ]
      .map((f) => readFileSync(resolve(root, f), 'utf8'))
      .join('\n');

    expect(docs).toContain('.\\scripts\\check-local-runtime.ps1');
    expect(docs).toContain('.\\scripts\\start-kokoro-tts.ps1');
    expect(docs).toContain('.\\scripts\\start-faster-whisper-stt.ps1');
    expect(docs).toContain('.\\scripts\\start-local-runtime.ps1');
  });
});
