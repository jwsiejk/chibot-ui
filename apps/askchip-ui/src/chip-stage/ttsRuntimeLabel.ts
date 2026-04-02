import type { ConfigResponse } from '../types/contract';

export function formatTtsRuntimeSummary(config: ConfigResponse): string {
  return `${config.tts_voice} · ${config.tts_device} · ${config.tts_provider}`;
}

export function formatTtsRuntimeWarning(config: ConfigResponse): string | null {
  if (config.tts_warning) {
    return config.tts_warning;
  }
  if (config.tts_fallback_reason) {
    return `${config.tts_fallback_reason} Using ${config.tts_provider}.`;
  }
  return null;
}
