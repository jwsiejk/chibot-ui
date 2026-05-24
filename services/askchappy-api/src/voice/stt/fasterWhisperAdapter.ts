import { getFasterWhisperConfig } from './fasterWhisperConfig';

export type SttResult =
  | { ok: true; text: string; provider: Record<string, unknown> }
  | { ok: false; code: 'not_configured' | 'runtime_unreachable' | 'no_speech' | 'invalid_response'; message: string };

export const transcribeWithFasterWhisper = async (audioBlob: Blob): Promise<SttResult> => {
  const config = getFasterWhisperConfig();
  if (!config.configured) {
    return { ok: false, code: 'not_configured', message: 'Local STT not configured. Set FASTER_WHISPER_BASE_URL and FASTER_WHISPER_MODEL.' };
  }

  const formData = new FormData();
  formData.append('file', audioBlob, `recording.${audioBlob.type.includes('webm') ? 'webm' : 'wav'}`);
  formData.append('model', config.model);
  formData.append('language', config.language);

  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), config.timeoutMs);
  try {
    const res = await fetch(`${config.baseUrl}/v1/transcribe`, {
      method: 'POST',
      body: formData,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      return { ok: false, code: 'runtime_unreachable', message: 'Local STT runtime is not reachable.' };
    }
    const payload = (await res.json()) as { text?: string };
    const text = payload.text?.trim() ?? '';
    if (!text) {
      return { ok: false, code: 'no_speech', message: 'No speech detected in recording.' };
    }
    return {
      ok: true,
      text,
      provider: { provider_id: 'local_faster_whisper', provider_label: 'Local faster-whisper', model: config.model, language: config.language },
    };
  } catch {
    return { ok: false, code: 'runtime_unreachable', message: 'Local STT runtime is not reachable.' };
  } finally {
    clearTimeout(t);
  }
};
