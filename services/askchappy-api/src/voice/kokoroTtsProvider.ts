import type { TtsProvider, TtsSynthesisInput, TtsSynthesisOutput } from './ttsProvider';
import { getKokoroTtsConfig } from './kokoroTtsConfig';

export const KOKORO_TTS_PROVIDER_ID = 'local_kokoro_onnx_tts';

const toUnavailable = (
  input: TtsSynthesisInput,
  reason: 'not_configured' | 'runtime_unreachable',
): TtsSynthesisOutput => ({
  audio_status: 'tts_unavailable',
  provider_id: KOKORO_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  spoken_text: input.text,
  audio_base64: null,
  audio_format: null,
  unavailable_reason: reason,
});

export const kokoroTtsProvider: TtsProvider = {
  provider_id: KOKORO_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  synthesize: async (input) => {
    const config = getKokoroTtsConfig();
    if (!config.configured) return toUnavailable(input, 'not_configured');

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), config.timeoutMs);

    try {
      const response = await fetch(`${config.baseUrl.replace(/\/$/, '')}/v1/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({ text: input.text, voice: config.voice, format: config.format }),
      });
      if (!response.ok) return toUnavailable(input, 'runtime_unreachable');
      const data = (await response.json()) as { audio_base64?: string };
      if (!data.audio_base64) return toUnavailable(input, 'runtime_unreachable');

      return {
        audio_status: 'ready',
        provider_id: KOKORO_TTS_PROVIDER_ID,
        provider_label: 'Standard voice',
        spoken_text: input.text,
        audio_base64: data.audio_base64,
        audio_format: config.format,
        provider_meta: { base_url: config.baseUrl, voice: config.voice },
      };
    } catch {
      return toUnavailable(input, 'runtime_unreachable');
    } finally {
      clearTimeout(timeoutId);
    }
  },
};
