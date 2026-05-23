import type { TtsProvider, TtsSynthesisInput, TtsSynthesisOutput } from './ttsProvider';

export const FALLBACK_TTS_PROVIDER_ID = 'local_fallback_tts';

const toDeterministicPlaceholder = (input: TtsSynthesisInput): TtsSynthesisOutput => ({
  audio_status: 'fallback_placeholder',
  provider_id: FALLBACK_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  spoken_text: input.text,
  audio_url: null,
});

export const fallbackTtsProvider: TtsProvider = {
  provider_id: FALLBACK_TTS_PROVIDER_ID,
  provider_label: 'Standard voice',
  synthesize: toDeterministicPlaceholder,
};
