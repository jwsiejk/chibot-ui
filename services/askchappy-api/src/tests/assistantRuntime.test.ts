import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getOllamaConfig } from '../assistant/config';
import { createLocalSession, appendLocalUserTextMessage, generateLocalAssistantMessage, getLocalTranscript } from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';

describe('phase 17 local ollama assistant runtime', () => {
  beforeEach(() => {
    resetSessionStore();
    vi.restoreAllMocks();
  });

  it('uses ollama config defaults', () => {
    expect(getOllamaConfig({})).toEqual({ baseUrl: 'http://127.0.0.1:11434', model: 'gemma3:4b', keepAlive: undefined, numCtx: undefined });
  });

  it('allows ollama config overrides', () => {
    expect(getOllamaConfig({ OLLAMA_BASE_URL: 'http://localhost:11435', OLLAMA_MODEL: 'gemma3:12b', OLLAMA_KEEP_ALIVE: '30m', OLLAMA_NUM_CTX: '8192' })).toEqual({ baseUrl: 'http://localhost:11435', model: 'gemma3:12b', keepAlive: '30m', numCtx: 8192 });
  });

  it('handles unreachable ollama without fake assistant transcript output', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'hello');
    const before = getLocalTranscript(session.session_id).length;

    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result.ok).toBe(false);
    expect(result.message).toContain('not configured or not reachable');
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('appends assistant transcript text from ollama response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ message: { content: 'Hi from local ollama' } }) });
    vi.stubGlobal('fetch', fetchMock);

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'Say hi');
    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result.ok).toBe(true);
    const transcript = getLocalTranscript(session.session_id);
    const last = transcript.at(-1);
    expect(last?.role).toBe('assistant');
    expect(last?.source).toBe('assistant_stream');
    expect(last?.text).toBe('Hi from local ollama');
    expect('content' in (last ?? {})).toBe(false);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.messages.at(-1).content).toBe('Say hi');
    expect(body.messages[0].content).toContain('Guided modes are overlays');
  });
});
