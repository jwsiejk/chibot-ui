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

describe('phase 21C windows local runtime startup scripts and venv/env guardrails', () => {
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
    expect(envExample).toContain('LOCAL_RUNTIME_PYTHON=.\\.venv-local-runtime\\Scripts\\python.exe');
    expect(envExample).toContain('LOCAL_RUNTIME_VENV=.venv-local-runtime');
    expect(envExample).toContain('KOKORO_TTS_ASSET_DIR=C:\\AskChipAssets\\kokoro');
    expect(envExample).toContain('KOKORO_TTS_RUN_COMMAND=');
    expect(envExample).toContain('FASTER_WHISPER_RUN_COMMAND=');
    expect(envExample.toLowerCase()).not.toContain('openai');
    expect(envExample.toLowerCase()).not.toContain('anthropic');
    expect(envExample.toLowerCase()).not.toContain('azure');
    expect(envExample.toLowerCase()).not.toContain('google');

    const gitignore = readFileSync(resolve(root, '.gitignore'), 'utf8');
    expect(gitignore).toContain('.env.local');
    expect(gitignore).toContain('.venv-local-runtime/');
    expect(gitignore).not.toContain('.env.example');
  });

  it('keeps start-local-runtime as preflight orchestrator and does not inline-exit after service script invocation', () => {
    const startLocal = readFileSync(resolve(root, 'scripts/start-local-runtime.ps1'), 'utf8');
    expect(startLocal).not.toContain("& (Join-Path $root 'scripts/start-kokoro-tts.ps1')");
    expect(startLocal).not.toContain("& (Join-Path $root 'scripts/start-faster-whisper-stt.ps1')");
    expect(startLocal).not.toContain('exit $LASTEXITCODE');
    expect(startLocal).toContain('.\\scripts\\start-kokoro-tts.ps1');
    expect(startLocal).toContain('.\\scripts\\start-faster-whisper-stt.ps1');
    expect(startLocal).toContain('.\\scripts\\check-local-runtime.ps1');
  });

  it('documents separate PowerShell windows for focused service startup before app launch', () => {
    const docs = [
      'README.md',
      'docs/LOCAL_FIRST_RUN_GUIDE.md',
      'docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md',
    ]
      .map((f) => readFileSync(resolve(root, f), 'utf8'))
      .join('\n');

    expect(docs).toContain('separate PowerShell windows');
    expect(docs).toContain('.\\scripts\\start-kokoro-tts.ps1');
    expect(docs).toContain('.\\scripts\\start-faster-whisper-stt.ps1');
    expect(docs).toContain('.\\scripts\\check-local-runtime.ps1');
    expect(docs).toContain('.\\scripts\\start-local-runtime.ps1');
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

  it('documents local runtime venv setup and local HTTP runtime requirements', () => {
    const docs = [
      'README.md',
      'docs/LOCAL_FIRST_RUN_GUIDE.md',
      'docs/LOCAL_RUNTIME_OPERATOR_GUIDE.md',
      'docs/CURRENT_IMPLEMENTATION_STATUS.md',
      'docs/LOCAL_FIRST_RELEASE_CHECKLIST.md',
    ]
      .map((f) => readFileSync(resolve(root, f), 'utf8'))
      .join('\n');

    expect(docs).toContain('.venv-local-runtime');
    expect(docs).toContain('onnxruntime-gpu');
    expect(docs).toContain('CUDAExecutionProvider');
    expect(docs).toContain('http://127.0.0.1:8880');
    expect(docs).toContain('http://127.0.0.1:8890');
    expect(docs).toContain('KOKORO_TTS_RUN_COMMAND');
    expect(docs).toContain('FASTER_WHISPER_RUN_COMMAND');
  });
});
