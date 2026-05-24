import type { LocalGpuServiceValidation, LocalGpuValidationReport } from '../../../../shared/contracts/gpu';
import { getOllamaConfig } from '../assistant/config';
import { getFasterWhisperConfig } from '../voice/stt/fasterWhisperConfig';
import { getKokoroTtsConfig } from '../voice/kokoroTtsConfig';

const normalize = (url: string) => url.replace(/\/$/, '');

const UNKNOWN_REASON =
  'GPU usage cannot be confirmed from this service API. Confirm with nvidia-smi or enable a local service GPU status endpoint.';

const getOllamaValidation = async (): Promise<LocalGpuServiceValidation> => {
  const config = getOllamaConfig();
  const suggested = ['nvidia-smi -l 1', `ollama run ${config.model}`];

  try {
    const res = await fetch(`${normalize(config.baseUrl)}/api/tags`);
    if (!res.ok) {
      return {
        service: 'ollama',
        status: 'runtime_unreachable',
        reason: `Local Ollama runtime is unreachable at ${normalize(config.baseUrl)}.`,
        suggested_commands: suggested,
      };
    }

    return {
      service: 'ollama',
      status: 'unknown',
      reason: `${UNKNOWN_REASON} Ollama /api/tags confirms runtime/model availability but not active GPU execution device.`,
      suggested_commands: suggested,
    };
  } catch {
    return {
      service: 'ollama',
      status: 'runtime_unreachable',
      reason: `Local Ollama runtime is unreachable at ${normalize(config.baseUrl)}.`,
      suggested_commands: suggested,
    };
  }
};

const getFasterWhisperValidation = async (): Promise<LocalGpuServiceValidation> => {
  const config = getFasterWhisperConfig();

  if (!config.configured) {
    return {
      service: 'faster_whisper',
      status: 'not_configured',
      reason: 'FASTER_WHISPER_BASE_URL and/or FASTER_WHISPER_MODEL are not configured for explicit local runtime validation.',
      suggested_commands: ['nvidia-smi -l 1'],
    };
  }

  try {
    const res = await fetch(`${normalize(config.baseUrl)}/health`);
    if (!res.ok) {
      return {
        service: 'faster_whisper',
        status: 'runtime_unreachable',
        reason: `faster-whisper runtime is unreachable at ${normalize(config.baseUrl)}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }

    const payload = (await res.json()) as Record<string, unknown>;
    const device = `${payload.device ?? payload.compute_type ?? payload.execution_provider ?? ''}`.toLowerCase();
    if (device.includes('cuda') || device.includes('gpu')) {
      return {
        service: 'faster_whisper',
        status: 'gpu_confirmed',
        reason: `faster-whisper health endpoint reports GPU-capable device/provider: ${device}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }
    if (device.includes('cpu')) {
      return {
        service: 'faster_whisper',
        status: 'cpu_only',
        reason: `faster-whisper health endpoint reports CPU execution: ${device}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }

    return {
      service: 'faster_whisper',
      status: 'unknown',
      reason: `${UNKNOWN_REASON} faster-whisper health endpoint did not include explicit device/provider fields.`,
      suggested_commands: ['nvidia-smi -l 1'],
    };
  } catch {
    return {
      service: 'faster_whisper',
      status: 'runtime_unreachable',
      reason: `faster-whisper runtime is unreachable at ${normalize(config.baseUrl)}.`,
      suggested_commands: ['nvidia-smi -l 1'],
    };
  }
};

const getKokoroValidation = async (): Promise<LocalGpuServiceValidation> => {
  const config = getKokoroTtsConfig();

  if (!config.configured) {
    return {
      service: 'kokoro_onnx',
      status: 'not_configured',
      reason: 'KOKORO_TTS_BASE_URL is not configured for explicit local runtime validation.',
      suggested_commands: ['nvidia-smi -l 1'],
    };
  }

  try {
    const res = await fetch(`${normalize(config.baseUrl)}/health`);
    if (!res.ok) {
      return {
        service: 'kokoro_onnx',
        status: 'runtime_unreachable',
        reason: `Kokoro runtime is unreachable at ${normalize(config.baseUrl)}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }

    const payload = (await res.json()) as Record<string, unknown>;
    const providers = Array.isArray(payload.providers) ? payload.providers.map((entry) => `${entry}`) : [];
    const normalizedProviders = providers.map((entry) => entry.toLowerCase());
    if (normalizedProviders.some((entry) => entry.includes('cudaexecutionprovider') || entry.includes('cuda'))) {
      return {
        service: 'kokoro_onnx',
        status: 'gpu_confirmed',
        reason: `Kokoro health endpoint reports GPU ONNX provider(s): ${providers.join(', ')}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }

    if (normalizedProviders.length > 0 && normalizedProviders.every((entry) => entry.includes('cpuexecutionprovider') || entry === 'cpu')) {
      return {
        service: 'kokoro_onnx',
        status: 'cpu_only',
        reason: `Kokoro health endpoint reports CPU-only ONNX provider(s): ${providers.join(', ')}.`,
        suggested_commands: ['nvidia-smi -l 1'],
      };
    }

    return {
      service: 'kokoro_onnx',
      status: 'unknown',
      reason: `${UNKNOWN_REASON} Kokoro health endpoint did not include explicit ONNX provider/device data.`,
      suggested_commands: ['nvidia-smi -l 1'],
    };
  } catch {
    return {
      service: 'kokoro_onnx',
      status: 'runtime_unreachable',
      reason: `Kokoro runtime is unreachable at ${normalize(config.baseUrl)}.`,
      suggested_commands: ['nvidia-smi -l 1'],
    };
  }
};

export const getLocalGpuValidationStatus = async (): Promise<LocalGpuValidationReport> => ({
  generated_at: new Date().toISOString(),
  services: await Promise.all([getOllamaValidation(), getFasterWhisperValidation(), getKokoroValidation()]),
  manual_guidance: [
    'Use nvidia-smi -l 1 in a separate terminal to watch GPU memory and utilization.',
    'Trigger Ollama, faster-whisper, and Kokoro workloads separately, then confirm corresponding local processes appear in nvidia-smi.',
    'Ollama and faster-whisper are the most important GPU candidates; Kokoro GPU is optional unless TTS latency is poor.',
    'AskChappy browser/Vite UI does not require GPU validation.',
  ],
});
