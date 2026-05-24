import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '../../../..');

const read = (rel: string) => readFileSync(path.join(repoRoot, rel), 'utf8');

describe('phase 21d local http runtime wrappers', () => {
  it('adds wrapper files and requirements', () => {
    expect(existsSync(path.join(repoRoot, 'services/local-runtime/kokoro_tts_server.py'))).toBe(true);
    expect(existsSync(path.join(repoRoot, 'services/local-runtime/faster_whisper_stt_server.py'))).toBe(true);
    expect(existsSync(path.join(repoRoot, 'services/local-runtime/requirements.txt'))).toBe(true);
  });

  it('uses local runtime packages only and avoids cloud/openai sdk names', () => {
    const req = read('services/local-runtime/requirements.txt').toLowerCase();
    expect(req).toContain('kokoro-onnx');
    expect(req).toContain('onnxruntime-gpu');
    expect(req).toContain('faster-whisper');
    expect(req).not.toMatch(/openai|azure|anthropic|google|aws|bedrock|elevenlabs/);
  });

  it('wrapper defaults bind localhost and include expected route strings', () => {
    const kokoro = read('services/local-runtime/kokoro_tts_server.py');
    const whisper = read('services/local-runtime/faster_whisper_stt_server.py');

    expect(kokoro).toContain('default="127.0.0.1"');
    expect(kokoro).toContain('default=8880');
    expect(whisper).toContain('default="127.0.0.1"');
    expect(whisper).toContain('default=8890');

    expect(kokoro).toContain('/health');
    expect(kokoro).toContain('/v1/health');
    expect(kokoro).toContain('/v1/tts');
    expect(whisper).toContain('/health');
    expect(whisper).toContain('/v1/transcribe');
  });



  it('adds local-only cors defaults for browser runtime checks', () => {
    const kokoro = read('services/local-runtime/kokoro_tts_server.py');
    const whisper = read('services/local-runtime/faster_whisper_stt_server.py');

    expect(kokoro).toContain('CORSMiddleware');
    expect(whisper).toContain('CORSMiddleware');
    expect(kokoro).toContain('http://127.0.0.1:4173');
    expect(kokoro).toContain('http://localhost:4173');
    expect(whisper).toContain('http://127.0.0.1:4173');
    expect(whisper).toContain('http://localhost:4173');
    expect(kokoro).not.toContain('allow_origins=["*"]');
    expect(whisper).not.toContain('allow_origins=["*"]');
  });

  it('includes explicit cuda validation logic without fake gpu claims', () => {
    const kokoro = read('services/local-runtime/kokoro_tts_server.py').toLowerCase();
    const whisper = read('services/local-runtime/faster_whisper_stt_server.py').toLowerCase();

    expect(kokoro).toContain('cudaexecutionprovider');
    expect(kokoro).toContain("requested provider 'cuda'");
    expect(whisper).toContain("requested device 'cuda'");
  });

  it('env example points run commands to committed wrappers', () => {
    const envExample = read('.env.example');
    expect(envExample).toContain('services\\local-runtime\\kokoro_tts_server.py');
    expect(envExample).toContain('services\\local-runtime\\faster_whisper_stt_server.py');
  });
});
