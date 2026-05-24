import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { getOllamaConfig } from '../assistant/config';
import {
  appendLocalUserTextMessage,
  createLocalSession,
  generateLocalAssistantMessage,
  getLocalSession,
  getLocalTranscript,
} from '../api/server';
import { hydrateSessionStoreFromPersistence, resetSessionStore } from '../sessions/sessionStore';

const packageJsonPath = resolve(process.cwd(), 'package.json');
const dependencyReviewPath = resolve(process.cwd(), 'docs/DEPENDENCY_REVIEW.md');

describe('phase 17 local ollama assistant runtime', () => {
  beforeEach(() => {
    resetSessionStore();
    vi.restoreAllMocks();
  });

  it('uses ollama config defaults', () => {
    expect(getOllamaConfig({})).toEqual({
      baseUrl: 'http://127.0.0.1:11434',
      model: 'gemma3:4b',
      keepAlive: undefined,
      numCtx: undefined,
    });
  });

  it('allows ollama config overrides', () => {
    expect(
      getOllamaConfig({
        OLLAMA_BASE_URL: 'http://localhost:11435',
        OLLAMA_MODEL: 'gemma3:12b',
        OLLAMA_KEEP_ALIVE: '30m',
        OLLAMA_NUM_CTX: '8192',
      }),
    ).toEqual({
      baseUrl: 'http://localhost:11435',
      model: 'gemma3:12b',
      keepAlive: '30m',
      numCtx: 8192,
    });
  });

  it('sends latest user message exactly once while preserving transcript context', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: { content: 'Got it' } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'Earlier context');
    appendLocalUserTextMessage(session.session_id, 'Latest request');

    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result.ok).toBe(true);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    const userMessages = body.messages.filter((message: { role: string }) => message.role === 'user');

    expect(userMessages).toEqual([
      { role: 'user', content: 'Earlier context' },
      { role: 'user', content: 'Latest request' },
    ]);
    expect(userMessages.filter((message: { content: string }) => message.content === 'Latest request')).toHaveLength(
      1,
    );
  });

  it('maps unreachable ollama to runtime_unavailable and does not append assistant transcript output', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'hello');
    const before = getLocalTranscript(session.session_id).length;

    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result).toMatchObject({ ok: false, code: 'runtime_unavailable' });
    expect(result.message).toContain('not configured or not reachable');
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('maps 404 from ollama to model_unavailable and does not append assistant transcript output', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }));

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'hello');
    const before = getLocalTranscript(session.session_id).length;

    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result).toMatchObject({ ok: false, code: 'model_unavailable' });
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('maps empty/invalid ollama payload to invalid_response and does not append assistant transcript output', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ message: { content: '   ' } }) }));

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'hello');
    const before = getLocalTranscript(session.session_id).length;

    const result = await generateLocalAssistantMessage(session.session_id);

    expect(result).toMatchObject({ ok: false, code: 'invalid_response' });
    expect(getLocalTranscript(session.session_id)).toHaveLength(before);
  });

  it('appends assistant transcript text from ollama response and persists after hydration', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ message: { content: 'Hi from local ollama' } }) }),
    );

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

    hydrateSessionStoreFromPersistence();
    const reloaded = getLocalSession(session.session_id);
    expect(reloaded?.transcript.at(-1)?.text).toBe('Hi from local ollama');
    expect('content' in (reloaded?.transcript.at(-1) ?? {})).toBe(false);
  });

  it('uses metadata.askchappy.session_mode in system instruction', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: { content: 'Response' } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const session = createLocalSession();
    session.metadata.askchappy.session_mode = 'meeting_prep';
    appendLocalUserTextMessage(session.session_id, 'Prep me');

    await generateLocalAssistantMessage(session.session_id);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.messages[0].role).toBe('system');
    expect(body.messages[0].content).toContain('Meeting prep mode');
  });

  it('includes voice-first conversational response policy in system instruction', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: { content: 'Response' } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'Explain ExaScaler');

    await generateLocalAssistantMessage(session.session_id);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    const systemInstruction = body.messages[0].content as string;

    expect(systemInstruction).toContain('live Zoom-style working session');
    expect(systemInstruction).toContain('2–4 short sentences, roughly 40–90 words');
    expect(systemInstruction).toContain('Ask at most one follow-up question');
    expect(systemInstruction).toContain('Do not dump long markdown explanations unless the user explicitly asks');
    expect(systemInstruction).toContain('Avoid large bullet lists unless explicitly requested');
    expect(systemInstruction).toContain('avoid overclaiming exact internals');
    expect(systemInstruction).toContain(
      'If the user asks for a technical explanation, stay technical but concise. Start with the core architecture idea, then offer the next layer.',
    );
  });

  it('does not introduce openai/cloud/rag dependencies or config', () => {
    const packageJson = readFileSync(packageJsonPath, 'utf8').toLowerCase();
    const dependencyReview = readFileSync(dependencyReviewPath, 'utf8').toLowerCase();

    expect(packageJson).not.toContain('openai');
    expect(packageJson).not.toContain('anthropic');
    expect(packageJson).not.toContain('langchain');
    expect(packageJson).not.toContain('pinecone');
    expect(packageJson).not.toContain('weaviate');

    expect(dependencyReview).toContain('no openai runtime dependency was added');
    expect(dependencyReview).toContain('no embeddings/vector database/rag dependency was added');
  });
});
