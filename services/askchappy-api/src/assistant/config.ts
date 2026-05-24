export type OllamaConfig = {
  baseUrl: string;
  model: string;
  keepAlive?: string;
  numCtx?: number;
  numPredict?: number;
  temperature?: number;
  topP?: number;
};

const DEFAULT_OLLAMA_BASE_URL = 'http://127.0.0.1:11434';
const DEFAULT_OLLAMA_MODEL = 'gemma3:4b';

export const getOllamaConfig = (
  env: Record<string, string | undefined> = process.env,
): OllamaConfig => {
  const parseIntIfFinite = (value?: string): number | undefined => {
    if (!value?.trim()) return undefined;
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  const parseNumberIfFinite = (value?: string): number | undefined => {
    if (!value?.trim()) return undefined;
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  return {
    baseUrl: env.OLLAMA_BASE_URL?.trim() || DEFAULT_OLLAMA_BASE_URL,
    model: env.OLLAMA_MODEL?.trim() || DEFAULT_OLLAMA_MODEL,
    keepAlive: env.OLLAMA_KEEP_ALIVE?.trim() || undefined,
    numCtx: parseIntIfFinite(env.OLLAMA_NUM_CTX),
    numPredict: parseIntIfFinite(env.OLLAMA_NUM_PREDICT),
    temperature: parseNumberIfFinite(env.OLLAMA_TEMPERATURE),
    topP: parseNumberIfFinite(env.OLLAMA_TOP_P),
  };
};
