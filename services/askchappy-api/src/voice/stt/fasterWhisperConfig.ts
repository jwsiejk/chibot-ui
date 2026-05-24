export type FasterWhisperConfig = {
  baseUrl: string;
  model: string;
  language: string;
  timeoutMs: number;
  configured: boolean;
};

const DEFAULT_TIMEOUT_MS = 20000;

export const getFasterWhisperConfig = (env: NodeJS.ProcessEnv = process.env): FasterWhisperConfig => {
  const baseUrl = env.FASTER_WHISPER_BASE_URL?.trim() ?? 'http://127.0.0.1:8890';
  const model = env.FASTER_WHISPER_MODEL?.trim() ?? 'base.en';
  const language = env.FASTER_WHISPER_LANGUAGE?.trim() || 'en';
  const timeoutMs = Number.parseInt(env.FASTER_WHISPER_TIMEOUT_MS ?? `${DEFAULT_TIMEOUT_MS}`, 10);

  return {
    baseUrl,
    model,
    language,
    timeoutMs: Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS,
    configured: Boolean(env.FASTER_WHISPER_BASE_URL?.trim() || env.FASTER_WHISPER_MODEL?.trim()),
  };
};
