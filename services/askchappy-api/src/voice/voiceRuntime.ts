import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import type { VoiceProfileState } from '../../../../shared/contracts/voice';
import { kokoroTtsProvider } from './kokoroTtsProvider';
import { getKokoroTtsConfig } from './kokoroTtsConfig';
import { getLocalClonedVoiceConfig } from './clonedVoiceConfig';
import { getVoiceProviderSelection } from './voiceProviderSelection';
import type { TtsSynthesisOutput } from './ttsProvider';

export type VoiceProfileRuntime = { id: string; state: VoiceProfileState };

export const getPublishedVoiceProfile = (
  profiles: readonly VoiceProfileRuntime[] = [],
): VoiceProfileRuntime | null => profiles.find((profile) => profile.state === 'published') ?? null;

const assertAssistantTranscriptMessage = (message: TranscriptMessage, sessionId: string): void => {
  if ((message as Record<string, unknown>).content !== undefined) {
    throw new Error('Invalid transcript message: content field is not allowed. Use text.');
  }
  if (message.role !== 'assistant') throw new Error('TTS requires assistant transcript messages.');
  if (message.session_id !== sessionId) throw new Error('TTS requires session_id to match transcript message session_id.');
  if (!message.id) throw new Error('TTS requires transcript message id.');
  if (!message.text.trim()) throw new Error('TTS requires non-empty assistant transcript text.');
};

export const synthesizeAssistantTranscriptMessage = async (
  input: { session_id: string; message: TranscriptMessage; voice_profiles?: readonly VoiceProfileRuntime[] },
): Promise<TtsSynthesisOutput> => {
  const profile = getPublishedVoiceProfile(input.voice_profiles ?? []);
  assertAssistantTranscriptMessage(input.message, input.session_id);

  const meta = input.message.meta as Record<string, unknown>;
  const spokenOverride = typeof meta?.tts_text === 'string' ? meta.tts_text : (typeof meta?.spoken_text === 'string' ? meta.spoken_text : null);

  return kokoroTtsProvider.synthesize({
    text: spokenOverride ?? input.message.text,
    session_id: input.session_id,
    message_id: input.message.id,
    voice_profile_id: profile?.id ?? null,
  });
};

export const getLocalVoiceRuntimeStatus = (
  profiles: readonly VoiceProfileRuntime[] = [],
): {
  active_provider_id: string;
  active_provider_label: string;
  published_voice_profile_state: VoiceProfileState | 'none';
  cloned_voice_ready: boolean;
  cloned_voice_reasons: string[];
  cloned_voice_status_label: string;
  standard_tts_configured: boolean;
} => {
  const published = getPublishedVoiceProfile(profiles);
  const selection = getVoiceProviderSelection({ clonedVoiceConfig: getLocalClonedVoiceConfig() });
  return {
    active_provider_id: kokoroTtsProvider.provider_id,
    active_provider_label: kokoroTtsProvider.provider_label,
    published_voice_profile_state: published?.state ?? 'none',
    cloned_voice_ready: selection.cloned_voice_ready,
    cloned_voice_reasons: selection.reasons,
    cloned_voice_status_label: selection.cloned_voice_status_label,
    standard_tts_configured: getKokoroTtsConfig().configured,
  };
};
