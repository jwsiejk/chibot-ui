import { buildChappySystemInstruction } from './chappyPersona';
import { getOllamaConfig } from './config';
import type { AssistantRuntimeRequest, AssistantRuntimeResult } from './contracts';
export const generateAssistantResponse = async (request: AssistantRuntimeRequest): Promise<AssistantRuntimeResult> => {
  const config = getOllamaConfig();
  const runtime = { provider: 'ollama_local' as const, model: config.model, base_url: config.baseUrl };
  const messages = [{ role: 'system', content: buildChappySystemInstruction(request.metadata.askchappy.session_mode) }, ...request.transcript.map((m) => ({ role: m.role, content: m.text })), { role: 'user', content: request.latest_user_text }];
  try {
    const response = await fetch(`${config.baseUrl}/api/chat`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model: config.model, stream: false, keep_alive: config.keepAlive, options: config.numCtx ? { num_ctx: config.numCtx } : undefined, messages }) });
    if (!response.ok) return { ok: false, code: response.status === 404 ? 'model_unavailable' : 'runtime_unavailable', message: response.status === 404 ? `Local Ollama model is not available: ${config.model}.` : 'Local Ollama runtime is not configured or not reachable.', runtime };
    const payload = (await response.json()) as { message?: { content?: string } };
    const text = payload.message?.content?.trim();
    if (!text) return { ok: false, code: 'invalid_response', message: 'Local Ollama returned an empty response.', runtime };
    return { ok: true, text, runtime };
  } catch {
    return { ok: false, code: 'runtime_unavailable', message: 'Local Ollama runtime is not configured or not reachable.', runtime };
  }
};
