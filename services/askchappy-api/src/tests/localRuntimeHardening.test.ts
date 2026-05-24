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
  });

  it('readiness checks never append transcript messages', async () => {
    const session = createLocalSession();
    const before = getLocalTranscript(session.session_id).length;
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const readiness = await getLocalRuntimeReadinessStatus();
    expect(readiness.ollama.status).toBe('unreachable');
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
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
