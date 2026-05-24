export type KokoroTtsConfig = {
  baseUrl: string;
  voice: string;
  format: string;
  timeoutMs: number;
  configured: boolean;
};

const DEFAULT_BASE_URL = 'http://127.0.0.1:8880';
const DEFAULT_VOICE = 'af_sarah';
const DEFAULT_FORMAT = 'wav';
const DEFAULT_TIMEOUT_MS = 8000;

export const getKokoroTtsConfig = (env: Record<string, string | undefined> = process.env): KokoroTtsConfig => {
  const timeoutMs = Number.parseInt(env.KOKORO_TTS_TIMEOUT_MS?.trim() ?? '', 10);
  const baseUrl = env.KOKORO_TTS_BASE_URL?.trim() || DEFAULT_BASE_URL;
  const voice = env.KOKORO_TTS_VOICE?.trim() || DEFAULT_VOICE;
  const format = env.KOKORO_TTS_FORMAT?.trim() || DEFAULT_FORMAT;

  return {
    baseUrl,
    voice,
    format,
    timeoutMs: Number.isFinite(timeoutMs) ? timeoutMs : DEFAULT_TIMEOUT_MS,
    configured: Boolean(env.KOKORO_TTS_BASE_URL?.trim()),
  };
};
