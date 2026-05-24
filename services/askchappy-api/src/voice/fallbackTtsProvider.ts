import type { TtsProvider, TtsSynthesisInput, TtsSynthesisOutput } from './ttsProvider';

export const FALLBACK_TTS_PROVIDER_ID = 'local_fallback_tts';

const toDeterministicPlaceholder = async (input: TtsSynthesisInput): Promise<TtsSynthesisOutput> => ({
  audio_status: 'tts_unavailable',
  provider_id: FALLBACK_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  spoken_text: input.text,
  audio_base64: null,
  audio_format: null,
  unavailable_reason: 'not_configured',
});

export const fallbackTtsProvider: TtsProvider = {
  provider_id: FALLBACK_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  synthesize: toDeterministicPlaceholder,
};
