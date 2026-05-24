import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getFasterWhisperConfig } from '../voice/stt/fasterWhisperConfig';
import { transcribeWithFasterWhisper } from '../voice/stt/fasterWhisperAdapter';
import { appendLocalUserTextMessage, createLocalSession, generateLocalAssistantMessage, getLocalTranscript, transcribeLocalVoiceInput } from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';

describe('phase 19 local faster-whisper stt', () => {
  beforeEach(() => {
    resetSessionStore();
    vi.restoreAllMocks();
    delete process.env.FASTER_WHISPER_BASE_URL;
    delete process.env.FASTER_WHISPER_MODEL;
    delete process.env.FASTER_WHISPER_LANGUAGE;
  });

  it('uses faster-whisper defaults', () => {
    expect(getFasterWhisperConfig({})).toMatchObject({ baseUrl: 'http://127.0.0.1:8890', model: 'base.en', language: 'en' });
  });

  it('supports faster-whisper overrides', () => {
    expect(getFasterWhisperConfig({ FASTER_WHISPER_BASE_URL: 'http://localhost:8891', FASTER_WHISPER_MODEL: 'small.en', FASTER_WHISPER_LANGUAGE: 'fr', FASTER_WHISPER_TIMEOUT_MS: '1234' })).toMatchObject({ baseUrl: 'http://localhost:8891', model: 'small.en', language: 'fr', timeoutMs: 1234, configured: true });
  });

  it('returns not_configured when env missing', async () => {
    const result = await transcribeWithFasterWhisper(new Blob(['x'], { type: 'audio/webm' }));
    expect(result).toMatchObject({ ok: false, code: 'not_configured' });
  });

  it('returns runtime_unreachable when local runtime cannot be reached', async () => {
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const result = await transcribeWithFasterWhisper(new Blob(['x'], { type: 'audio/webm' }));
    expect(result).toMatchObject({ ok: false, code: 'runtime_unreachable' });
  });

  it('returns transcript text and appends canonical voice user message', async () => {
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: 'voice text' }) }));

    const session = createLocalSession();
    const out = await transcribeLocalVoiceInput(session.session_id, new Blob(['x'], { type: 'audio/webm' }));

    expect(out.ok).toBe(true);
    if (out.ok) {
      expect(out.message.source).toBe('voice');
      expect(out.message.text).toBe('voice text');
      expect('content' in out.message).toBe(false);
    }
  });

  it('no speech does not append fake transcript messages', async () => {
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: '   ' }) }));
    const session = createLocalSession();
    const before = getLocalTranscript(session.session_id).length;
    const out = await transcribeLocalVoiceInput(session.session_id, new Blob(['x'], { type: 'audio/webm' }));
    expect(out).toMatchObject({ ok: false, code: 'no_speech' });
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('typed input flow still works and voice triggers same assistant path', async () => {
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ text: 'spoken hello' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ message: { content: 'assistant reply' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ message: { content: 'assistant typed' } }) }));

    const session = createLocalSession();
    await transcribeLocalVoiceInput(session.session_id, new Blob(['x'], { type: 'audio/webm' }));
    await generateLocalAssistantMessage(session.session_id);
    appendLocalUserTextMessage(session.session_id, 'typed hello');
    await generateLocalAssistantMessage(session.session_id);

    const transcript = getLocalTranscript(session.session_id);
    expect(transcript.some((m) => m.source === 'voice' && m.text === 'spoken hello')).toBe(true);
    expect(transcript.some((m) => m.source === 'typed' && m.text === 'typed hello')).toBe(true);
    expect(transcript.filter((m) => m.role === 'assistant').length).toBe(2);
  });
});
