import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  appendLocalUserTextMessage,
  createLocalSession,
  generateLocalAssistantMessage,
  getLocalRuntimeReadinessStatus,
  getLocalTranscript,
  synthesizeLocalAssistantMessage,
  transcribeLocalVoiceInput,
} from '../api/server';
import { hydrateSessionStoreFromPersistence, resetSessionStore } from '../sessions/sessionStore';

describe('phase 20A local runtime hardening and readiness', () => {
  beforeEach(() => {
    resetSessionStore();
    vi.restoreAllMocks();
    delete process.env.KOKORO_TTS_BASE_URL;
    delete process.env.FASTER_WHISPER_BASE_URL;
    delete process.env.FASTER_WHISPER_MODEL;
    delete process.env.OLLAMA_BASE_URL;
    delete process.env.OLLAMA_MODEL;
  });

  it('readiness checks never append transcript messages', async () => {
    const session = createLocalSession();
    const before = getLocalTranscript(session.session_id).length;
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.ollama.status).toBe('unreachable');
    expect(readiness.ollama.reason).toContain('http://127.0.0.1:11434');
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('kokoro readiness uses /health first and skips synthetic fallback when health succeeds', async () => {
    process.env.KOKORO_TTS_BASE_URL = 'http://127.0.0.1:8880';
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('ollama offline'))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);

    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.kokoro_tts.status).toBe('ready');
    expect(readiness.kokoro_tts.reason).toBe('Kokoro local TTS runtime reachable via health probe.');
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8880/health');
    expect(fetchMock).not.toHaveBeenCalledWith('http://127.0.0.1:8880/v1/tts', expect.anything());
  });

  it('kokoro readiness tries /v1/health when /health is unsupported', async () => {
    process.env.KOKORO_TTS_BASE_URL = 'http://127.0.0.1:8880';
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('ollama offline'))
      .mockResolvedValueOnce({ ok: false, status: 404 })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) });
    vi.stubGlobal('fetch', fetchMock);

    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.kokoro_tts.status).toBe('ready');
    expect(readiness.kokoro_tts.reason).toBe('Kokoro local TTS runtime reachable via health probe.');
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8880/health');
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8880/v1/health');
    expect(fetchMock).not.toHaveBeenCalledWith('http://127.0.0.1:8880/v1/tts', expect.anything());
  });

  it('kokoro readiness falls back to synthetic /v1/tts only when health probes are unsupported', async () => {
    process.env.KOKORO_TTS_BASE_URL = 'http://127.0.0.1:8880';
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('ollama offline'))
      .mockResolvedValueOnce({ ok: false, status: 404 })
      .mockResolvedValueOnce({ ok: false, status: 405 })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ audio_base64: 'synthetic-audio', spoken_text: 'synthetic' }) });
    vi.stubGlobal('fetch', fetchMock);

    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.kokoro_tts.status).toBe('ready');
    expect(readiness.kokoro_tts.reason).toBe('Kokoro local TTS runtime reachable via synthetic readiness fallback; fixed non-user text used and output discarded.');
    expect(JSON.stringify(readiness)).not.toContain('audio_base64');
    expect(JSON.stringify(readiness)).not.toContain('spoken_text');

    const kokoroCall = fetchMock.mock.calls[3] as [string, { method: string; body: string }];
    expect(kokoroCall[0]).toBe('http://127.0.0.1:8880/v1/tts');
    expect(kokoroCall[1].method).toBe('POST');
    expect(kokoroCall[1].body).toContain('askchappy_local_runtime_readiness_probe');
  });

  it('ollama model check reports model_unavailable when runtime is reachable but model is absent', async () => {
    process.env.OLLAMA_MODEL = 'model-that-does-not-exist';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ models: [{ name: 'gemma3:4b' }] }) }));

    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.ollama.status).toBe('model_unavailable');
    expect(readiness.ollama.reason).toContain('model-that-does-not-exist');
  });

  it('full mocked voice loop commits canonical transcript and exact spoken_text', async () => {
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    process.env.KOKORO_TTS_BASE_URL = 'http://127.0.0.1:8880';

    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ text: 'voice user' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ message: { content: 'assistant says hi' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ audio_base64: 'abc123' }) }),
    );

    const session = createLocalSession();
    const stt = await transcribeLocalVoiceInput(session.session_id, new Blob(['x'], { type: 'audio/webm' }));
    expect(stt.ok).toBe(true);
    await generateLocalAssistantMessage(session.session_id);

    const transcript = getLocalTranscript(session.session_id);
    const user = transcript.find((m) => m.role === 'user');
    const assistant = transcript.find((m) => m.role === 'assistant');
    expect(user?.source).toBe('voice');
    expect(user?.text).toBe('voice user');
    expect(assistant?.text).toBe('assistant says hi');

    const tts = await synthesizeLocalAssistantMessage(session.session_id, assistant!.id);
    expect(tts.spoken_text).toBe(assistant?.text);

    hydrateSessionStoreFromPersistence();
    expect(getLocalTranscript(session.session_id).length).toBe(2);
  });

  it('ollama/tts failures do not append fake assistant messages and typed input still works', async () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'typed works');
    const before = getLocalTranscript(session.session_id).length;
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const fail = await generateLocalAssistantMessage(session.session_id);
    expect(fail.ok).toBe(false);
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });
});
