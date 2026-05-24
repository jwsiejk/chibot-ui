import { getOllamaConfig } from '../assistant/config';
import { getKokoroTtsConfig } from '../voice/kokoroTtsConfig';
import { getFasterWhisperConfig } from '../voice/stt/fasterWhisperConfig';
import { getVoiceProviderSelection } from '../voice/voiceProviderSelection';
import { getLocalClonedVoiceConfig } from '../voice/clonedVoiceConfig';

export type ServiceStatus = 'ready' | 'not_configured' | 'unreachable' | 'model_unavailable';

export type LocalRuntimeReadiness = {
  ollama: { status: ServiceStatus; reason: string };
  kokoro_tts: { status: Exclude<ServiceStatus, 'model_unavailable'>; reason: string };
  faster_whisper_stt: { status: Exclude<ServiceStatus, 'model_unavailable'>; reason: string };
  standard_voice: { status: 'selected_default'; reason: string };
  cloned_voice: { status: 'optional_gated'; reason: string };
};

const normalize = (url: string) => url.replace(/\/$/, '');

export const getLocalRuntimeReadiness = async (): Promise<LocalRuntimeReadiness> => {
  const ollamaConfig = getOllamaConfig();
  const kokoroConfig = getKokoroTtsConfig();
  const sttConfig = getFasterWhisperConfig();
  const cloned = getVoiceProviderSelection({ clonedVoiceConfig: getLocalClonedVoiceConfig() });

  const readiness: LocalRuntimeReadiness = {
    ollama: { status: 'not_configured', reason: 'OLLAMA_BASE_URL or OLLAMA_MODEL is missing.' },
    kokoro_tts: { status: 'not_configured', reason: 'KOKORO_TTS_BASE_URL is not configured.' },
    faster_whisper_stt: { status: 'not_configured', reason: 'FASTER_WHISPER_BASE_URL and FASTER_WHISPER_MODEL are not configured.' },
    standard_voice: { status: 'selected_default', reason: 'Standard local voice remains selected/default.' },
    cloned_voice: {
      status: 'optional_gated',
      reason: cloned.cloned_voice_ready ? 'Cloned voice is optional and readiness-gated.' : `Cloned voice optional/gated: ${cloned.reasons.join(', ') || 'not configured'}.`,
    },
  };

  if (ollamaConfig.baseUrl.trim() && ollamaConfig.model.trim()) {
    try {
      const res = await fetch(`${normalize(ollamaConfig.baseUrl)}/api/tags`);
      if (!res.ok) {
        readiness.ollama = { status: 'unreachable', reason: 'Local Ollama runtime is unreachable.' };
      } else {
        const payload = (await res.json()) as { models?: Array<{ name?: string; model?: string }> };
        const modelAvailable = (payload.models ?? []).some((m) => (m.name ?? m.model ?? '').includes(ollamaConfig.model));
        readiness.ollama = modelAvailable
          ? { status: 'ready', reason: `Local Ollama ready with model ${ollamaConfig.model}.` }
          : { status: 'model_unavailable', reason: `Configured model not available: ${ollamaConfig.model}.` };
      }
    } catch {
      readiness.ollama = { status: 'unreachable', reason: 'Local Ollama runtime is unreachable.' };
    }
  }

  if (kokoroConfig.configured) {
    try {
      const res = await fetch(`${normalize(kokoroConfig.baseUrl)}/v1/tts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: 'readiness_check', voice: kokoroConfig.voice, format: kokoroConfig.format }),
      });
      readiness.kokoro_tts = res.ok
        ? { status: 'ready', reason: 'Kokoro local TTS runtime reachable.' }
        : { status: 'unreachable', reason: 'Kokoro local TTS runtime unreachable.' };
    } catch {
      readiness.kokoro_tts = { status: 'unreachable', reason: 'Kokoro local TTS runtime unreachable.' };
    }
  }

  if (sttConfig.configured) {
    try {
      const res = await fetch(`${normalize(sttConfig.baseUrl)}/health`);
      readiness.faster_whisper_stt = res.ok
        ? { status: 'ready', reason: 'faster-whisper local STT runtime reachable.' }
        : { status: 'unreachable', reason: 'faster-whisper local STT runtime unreachable.' };
    } catch {
      readiness.faster_whisper_stt = { status: 'unreachable', reason: 'faster-whisper local STT runtime unreachable.' };
    }
  }

  return readiness;
};
