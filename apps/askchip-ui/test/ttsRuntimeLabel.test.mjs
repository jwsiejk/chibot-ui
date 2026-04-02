import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { formatTtsRuntimeSummary, formatTtsRuntimeWarning } from '../.test-dist/chip-stage/ttsRuntimeLabel.js';

function config(overrides = {}) {
  return {
    app_name: 'AskChip',
    ollama_base_url: '',
    ollama_model: 'gemma3:4b',
    database_path: '',
    stt_model: 'base',
    stt_device: 'cuda',
    stt_compute_type: 'int8_float16',
    tts_voice: 'am_echo',
    tts_requested_device: 'auto',
    tts_device: 'cuda',
    tts_provider: 'CUDAExecutionProvider',
    tts_available_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
    tts_warning: null,
    tts_fallback_reason: null,
    tts_model_path: null,
    tts_voices_path: null,
    tts_sample_rate_hz: 24000,
    tts_speed: 1,
    tts_lang_code: 'en-us',
    local_only: true,
    ollama_warmup_enabled: true,
    tts_warmup_enabled: false,
    ...overrides,
  };
}

describe('ttsRuntimeLabel', () => {
  it('renders concise voice/device/provider summary', () => {
    assert.equal(formatTtsRuntimeSummary(config()), 'am_echo · cuda · CUDAExecutionProvider');
  });

  it('surfaces explicit fallback warning when provided', () => {
    const warning = formatTtsRuntimeWarning(config({
      tts_device: 'cpu',
      tts_provider: 'CPUExecutionProvider',
      tts_warning: 'ASKCHIP_TTS_DEVICE=auto requested but CUDAExecutionProvider is unavailable. Using CPUExecutionProvider.',
    }));
    assert.match(warning, /CUDAExecutionProvider is unavailable/);
  });
});
