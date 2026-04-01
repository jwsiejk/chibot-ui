const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';
const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8000';

function normalizeBaseUrl(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  if (!trimmed) {
    return fallback;
  }

  return trimmed.replace(/\/$/, '');
}

const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};

export const runtimeConfig = {
  apiBaseUrl: normalizeBaseUrl(env.VITE_ASKCHIP_API_BASE_URL, DEFAULT_API_BASE_URL),
  wsBaseUrl: normalizeBaseUrl(env.VITE_ASKCHIP_WS_BASE_URL, DEFAULT_WS_BASE_URL),
  assistantDisplayName: env.VITE_ASKCHIP_ASSISTANT_DISPLAY_NAME?.trim() || 'Chip',
};
