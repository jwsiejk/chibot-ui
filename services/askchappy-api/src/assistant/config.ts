export type OllamaConfig = { baseUrl: string; model: string; keepAlive?: string; numCtx?: number; };
const DEFAULT_OLLAMA_BASE_URL = 'http://127.0.0.1:11434';
const DEFAULT_OLLAMA_MODEL = 'gemma3:4b';
export const getOllamaConfig = (env: Record<string, string | undefined> = process.env): OllamaConfig => {
  const parsedNumCtx = env.OLLAMA_NUM_CTX?.trim() ? Number.parseInt(env.OLLAMA_NUM_CTX, 10) : undefined;
  return { baseUrl: env.OLLAMA_BASE_URL?.trim() || DEFAULT_OLLAMA_BASE_URL, model: env.OLLAMA_MODEL?.trim() || DEFAULT_OLLAMA_MODEL, keepAlive: env.OLLAMA_KEEP_ALIVE?.trim() || undefined, numCtx: Number.isFinite(parsedNumCtx) ? parsedNumCtx : undefined };
};
