const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';
const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8000';

function normalizeBaseUrl(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  if (!trimmed) {
    return fallback;
  }

  return trimmed.replace(/\/$/, '');
}

export const runtimeConfig = {
  apiBaseUrl: normalizeBaseUrl(import.meta.env.VITE_ASKCHIP_API_BASE_URL, DEFAULT_API_BASE_URL),
  wsBaseUrl: normalizeBaseUrl(import.meta.env.VITE_ASKCHIP_WS_BASE_URL, DEFAULT_WS_BASE_URL),
};
