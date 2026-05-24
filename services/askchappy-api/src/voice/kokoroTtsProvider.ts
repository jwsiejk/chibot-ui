import type { TtsProvider, TtsSynthesisInput, TtsSynthesisOutput } from './ttsProvider';
import { getKokoroTtsConfig } from './kokoroTtsConfig';

export const KOKORO_TTS_PROVIDER_ID = 'local_kokoro_onnx_tts';

const toUnavailable = (
  input: TtsSynthesisInput,
  reason: 'not_configured' | 'runtime_unreachable' | 'request_cancelled' | 'request_rejected' | 'synthesis_failed' | 'invalid_response',
  message?: string,
  detail?: string,
): TtsSynthesisOutput => ({
  audio_status: 'tts_unavailable',
  provider_id: KOKORO_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  spoken_text: input.text,
  audio_base64: null,
  audio_format: null,
  unavailable_reason: reason,
  unavailable_message: message,
  provider_error_detail: detail,
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
      const data = (await response.json()) as { audio_base64?: string; detail?: string; error?: string };
      const safeDetail = typeof data.detail === 'string' ? data.detail.slice(0, 240) : undefined;
      if (!response.ok) {
        if (response.status === 400) return toUnavailable(input, 'request_rejected', 'Chappy voice request was rejected by Kokoro runtime.', safeDetail);
        if (response.status >= 500) return toUnavailable(input, 'synthesis_failed', 'Chappy voice synthesis failed. Check Kokoro service logs.', safeDetail);
        return toUnavailable(input, 'runtime_unreachable', 'Chappy voice runtime unreachable.', safeDetail);
      }
      if (!data.audio_base64) return toUnavailable(input, 'invalid_response', 'Chappy voice response was invalid.', safeDetail);

      return {
        audio_status: 'ready',
        provider_id: KOKORO_TTS_PROVIDER_ID,
        provider_label: 'Standard voice',
        spoken_text: input.text,
        audio_base64: data.audio_base64,
        audio_format: config.format,
        provider_meta: { base_url: config.baseUrl, voice: config.voice },
      };
    } catch (error) {
      if (error instanceof Error && (error.name === 'AbortError' || error.message.toLowerCase().includes('abort'))) {
        return toUnavailable(input, 'request_cancelled', 'Chappy voice request was canceled.');
      }
      return toUnavailable(input, 'runtime_unreachable', 'Chappy voice runtime unreachable.');
    } finally {
      clearTimeout(timeoutId);
    }
  },
};
