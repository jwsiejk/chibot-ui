import { describe, expect, it, beforeEach } from 'vitest';
import {
  appendLocalTranscriptMessage,
  appendLocalUserTextMessage,
  createLocalSession,
  generateLocalAssistantMessage,
  getHealth,
  getLocalSession,
  getLocalTranscript,
  getLocalVoiceStatus,
  setLocalSessionMode,
  synthesizeLocalAssistantMessage,
} from '../api/server';
import { hydrateSessionStoreFromPersistence, resetSessionStore } from '../sessions/sessionStore';
import {
  BROWSER_LOCAL_SESSION_SCHEMA_VERSION,
  BROWSER_LOCAL_SESSION_STORAGE_KEY,
} from '../sessions/browserLocalSessionPersistenceAdapter';
import { DEFAULT_SESSION_MODE } from '../../../../shared/contracts/modes';
import { CREATE_PRESENTATIONS_INTRO_MESSAGE } from '../../../../shared/contracts/createPresentationsMode';

describe('askchappy-api scaffold', () => {
  beforeEach(() => {
    resetSessionStore();
  });

  it('returns placeholder health', () => {
    expect(getHealth()).toEqual({ service: 'askchappy-api', status: 'placeholder' });
  });

  it('creates local session with default askchappy metadata and open_qa mode', () => {
    const session = createLocalSession();

    expect(session.metadata.askchappy).toBeDefined();
    expect(session.metadata.askchappy.session_mode).toBe(DEFAULT_SESSION_MODE);
    expect('expert_desk' in session.metadata).toBe(false);
  });

  it('loads an existing local session by id', () => {
    const session = createLocalSession();

    expect(getLocalSession(session.session_id)).toBe(session);
  });


  it('creates isolated metadata objects per session', () => {
    const first = createLocalSession();
    const second = createLocalSession();

    first.metadata.askchappy.context.customer_name = 'Acme Corp';

    expect(second.metadata.askchappy.context.customer_name).toBeNull();
    expect(first.metadata).not.toBe(second.metadata);
    expect(first.metadata.askchappy).not.toBe(second.metadata.askchappy);
    expect(first.metadata.askchappy.context).not.toBe(second.metadata.askchappy.context);
  });

  it('appends typed user transcript messages with canonical text field', () => {
    const session = createLocalSession();

    const message = appendLocalUserTextMessage(session.session_id, 'hello from typed input');

    expect(message.role).toBe('user');
    expect(message.source).toBe('typed');
    expect(message.text).toBe('hello from typed input');
    expect('content' in message).toBe(false);
  });

  it('returns transcript entries in append order', () => {
    const session = createLocalSession();

    const first = appendLocalUserTextMessage(session.session_id, 'first');
    const second = appendLocalUserTextMessage(session.session_id, 'second');

    expect(getLocalTranscript(session.session_id)).toEqual([first, second]);
  });

  it('keeps session events separate from transcript messages', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'event separation check');

    const loaded = getLocalSession(session.session_id);
    expect(loaded?.events).toHaveLength(2);
    expect(loaded?.transcript).toHaveLength(1);
    expect(loaded?.events[1]?.event_type).toBe('transcript_message_appended');
  });

  it('records session_created event on session creation', () => {
    const session = createLocalSession();

    expect(session.events).toHaveLength(1);
    expect(session.events[0]?.event_type).toBe('session_created');
  });

  it('records transcript_message_appended event without polluting transcript', () => {
    const session = createLocalSession();

    const appended = appendLocalUserTextMessage(session.session_id, 'track transcript event');
    const loaded = getLocalSession(session.session_id);

    expect(loaded?.events[1]?.event_type).toBe('transcript_message_appended');
    expect(loaded?.events[1]?.meta).toEqual({ message_id: appended.id });
    expect(loaded?.transcript).toEqual([appended]);
  });

  it('rejects invalid transcript messages and content field payloads', () => {
    const session = createLocalSession();

    expect(() =>
      appendLocalTranscriptMessage(session.session_id, {
        id: 'msg_invalid',
        ts: new Date().toISOString(),
        role: 'assistant',
        source: 'assistant_stream',
        session_id: session.session_id,
        meta: {},
        content: 'invalid field',
      } as unknown as never),
    ).toThrowError('Invalid transcript message: must match canonical TranscriptMessage contract.');
  });


  it('updates metadata mode and records mode_change event without transcript pollution', () => {
    const session = createLocalSession();
    const existing = appendLocalUserTextMessage(session.session_id, 'before switching');

    const updated = setLocalSessionMode(session.session_id, 'meeting_prep', 'user');

    expect(updated.session_id).toBe(session.session_id);
    expect(updated.metadata.askchappy.session_mode).toBe('meeting_prep');
    expect(updated.metadata.askchappy.persona_id).toBe('ddn_chappy_vptm');
    expect(updated.metadata.askchappy.persona_label).toBe('Chappy');
    expect(updated.transcript).toEqual([existing]);

    const modeEvent = updated.events.at(-1);
    expect(modeEvent?.event_type).toBe('mode_change');
    expect(modeEvent?.meta).toMatchObject({ from_mode: 'open_qa', to_mode: 'meeting_prep', actor: 'user' });
  });

  it('initializes create_presentations mode state and intro assistant prompt on mode entry', () => {
    const session = createLocalSession();
    const updated = setLocalSessionMode(session.session_id, 'create_presentations', 'user');

    expect(updated.metadata.askchappy.session_mode).toBe('create_presentations');
    expect(updated.metadata.askchappy.create_presentations_state).toEqual({
      active: true,
      mode: 'create_presentations',
      step: 'intro',
      deckBrief: expect.objectContaining({
        schema_version: '1.0',
        mode: 'create_presentations',
        status: 'draft',
        output: { format: 'pptx' },
        source_requirements: {
          source_policy: 'user_provided_only',
          citations_required: false,
          allowed_source_types: ['manual_notes'],
        },
      }),
      awaitingUserInput: true,
      outline: { status: 'not_started', slides: [] },
      generatedPresentation: { status: 'not_started', format: 'pptx' },
      skippedFields: [],
      events: expect.arrayContaining([expect.objectContaining({ kind: 'mode_entered', actor: 'system' })]),
    });
    expect(updated.transcript.at(-1)?.role).toBe('assistant');
    expect(updated.transcript.at(-1)?.text).toBe(CREATE_PRESENTATIONS_INTRO_MESSAGE);
  });


  it('synthesizes assistant transcript text via standard voice provider without creating new transcript messages', async () => {
    const session = createLocalSession();
    const assistantMessage = appendLocalTranscriptMessage(session.session_id, {
      id: 'msg_assistant_tts',
      ts: new Date().toISOString(),
      role: 'assistant',
      text: 'Speak the canonical assistant transcript.',
      source: 'assistant_stream',
      session_id: session.session_id,
      meta: {},
    });

    const beforeCount = getLocalTranscript(session.session_id).length;
    const output = await synthesizeLocalAssistantMessage(session.session_id, assistantMessage.id);
    const afterCount = getLocalTranscript(session.session_id).length;

    expect(output.spoken_text).toBe('Speak the canonical assistant transcript.');
    expect(output.provider_id).toBe('local_kokoro_onnx_tts');
    expect(afterCount).toBe(beforeCount);
  });


  it('reports standard voice active by default with no cloned voice profile configured', () => {
    const status = getLocalVoiceStatus();

    expect(status.active_provider_id).toBe('local_kokoro_onnx_tts');
    expect(status.active_provider_label).toBe('Standard voice');
    expect(status.published_voice_profile_state).toBe('none');
  });

  it('rejects invalid mode changes through service validation', () => {
    const session = createLocalSession();
    expect(() => setLocalSessionMode(session.session_id, 'bad_mode' as never, 'user')).toThrowError(
      'Invalid session mode: bad_mode',
    );
  });

  it('persists sessions, transcript text, metadata.askchappy, and session events across reload-style hydration', () => {
    const session = createLocalSession();
    const message = appendLocalUserTextMessage(session.session_id, 'persist this text');
    setLocalSessionMode(session.session_id, 'learn_ddn', 'user');

    hydrateSessionStoreFromPersistence();
    const loaded = getLocalSession(session.session_id);

    expect(loaded?.metadata.askchappy.session_mode).toBe('learn_ddn');
    expect(loaded?.transcript[0]?.text).toBe('persist this text');
    expect(loaded?.transcript[0]).toMatchObject({ id: message.id, source: 'typed', session_id: session.session_id });
    expect('content' in (loaded?.transcript[0] ?? {})).toBe(false);
    expect(loaded?.events.some((event) => event.event_type === 'mode_change')).toBe(true);
    expect(loaded?.transcript.every((entry) => entry.text !== 'mode_change')).toBe(true);
  });

  it('safely recovers from malformed persisted payloads', () => {
    const storage = window.localStorage;
    storage.setItem(BROWSER_LOCAL_SESSION_STORAGE_KEY, '{bad-json');

    expect(() => hydrateSessionStoreFromPersistence()).not.toThrow();
    expect(storage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('no-ops safely when browser localStorage is unavailable', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get: () => {
        throw new Error('blocked storage');
      },
    });

    expect(() => createLocalSession()).not.toThrow();
    expect(() => hydrateSessionStoreFromPersistence()).not.toThrow();
    expect(() => resetSessionStore()).not.toThrow();

    if (original) Object.defineProperty(window, 'localStorage', original);
  });

  it('removes only malformed local persistence payload and leaves unrelated keys', () => {
    const storage = window.localStorage;
    storage.setItem('askchappy.unrelated.key', 'keep-me');
    storage.setItem(BROWSER_LOCAL_SESSION_STORAGE_KEY, JSON.stringify({ schema_version: 99, sessions: [] }));

    hydrateSessionStoreFromPersistence();

    expect(storage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY)).toBeNull();
    expect(storage.getItem('askchappy.unrelated.key')).toBe('keep-me');
  });

  it('rejects persisted transcript payloads that use content instead of text', () => {
    const storage = window.localStorage;
    const now = new Date().toISOString();
    storage.setItem(
      BROWSER_LOCAL_SESSION_STORAGE_KEY,
      JSON.stringify({
        schema_version: BROWSER_LOCAL_SESSION_SCHEMA_VERSION,
        sessions: [
          {
            session_id: 'session_bad_content',
            created_at: now,
            updated_at: now,
            metadata: {
              askchappy: {
                session_mode: 'open_qa',
                persona_id: 'ddn_chappy_vptm',
                persona_label: 'Chappy',
                context: { customer_name: null, issue_summary: null, locale: null },
              },
            },
            transcript: [
              {
                id: 'msg1',
                ts: now,
                role: 'user',
                source: 'typed',
                session_id: 'session_bad_content',
                meta: {},
                content: 'invalid',
              },
            ],
            events: [],
          },
        ],
      }),
    );

    hydrateSessionStoreFromPersistence();

    expect(storage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY)).toBeNull();
    expect(getLocalSession('session_bad_content')).toBeUndefined();
  });

  it('keeps schema-versioned browser-local persistence payload', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'schema check');

    const persisted = JSON.parse(window.localStorage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY) ?? '{}');
    expect(persisted.schema_version).toBe(BROWSER_LOCAL_SESSION_SCHEMA_VERSION);
  });

  it('reset clears in-memory sessions and browser-local payload', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'to be cleared');

    expect(window.localStorage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY)).not.toBeNull();

    resetSessionStore();

    expect(getLocalSession(session.session_id)).toBeUndefined();
    expect(window.localStorage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('voice runtime remains standard-default and cloned voice optional after hydration', () => {
    const session = createLocalSession();
    appendLocalUserTextMessage(session.session_id, 'voice status check');

    hydrateSessionStoreFromPersistence();

    const status = getLocalVoiceStatus();
    expect(status.active_provider_id).toBe('local_kokoro_onnx_tts');
    expect(status.active_provider_label).toBe('Standard voice');
    expect(status.published_voice_profile_state).toBe('none');
    expect(status.cloned_voice_ready).toBe(false);
  });



  it('routes create_presentations user turns through guided interview and supports approval', async () => {
    const session = createLocalSession();
    setLocalSessionMode(session.session_id, 'create_presentations', 'user');
    const answers = [
      'customer_executive_briefing','Q3 modernization', 'CIO team', 'Healthcare account', 'healthcare', 'cyber resilience',
      '10', 'executive', 'medium', 'business drivers, architecture', 'keep concise', 'risk reduction', 'focus on outcomes', 'yes',
    ];
    for (const a of answers) { appendLocalUserTextMessage(session.session_id, a); await generateLocalAssistantMessage(session.session_id); }
    const reviewMsg = getLocalTranscript(session.session_id).at(-1);
    expect(reviewMsg?.text).toContain('Approve this brief, or tell me what to revise.');
    appendLocalUserTextMessage(session.session_id, 'approve');
    await generateLocalAssistantMessage(session.session_id);
    const updated = getLocalSession(session.session_id);
    expect(updated?.metadata.askchappy.create_presentations_state?.deckBrief.status).toBe('brief_approved');
  });
});
