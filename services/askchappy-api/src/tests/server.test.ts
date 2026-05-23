import { describe, expect, it, beforeEach } from 'vitest';
import {
  appendLocalTranscriptMessage,
  appendLocalUserTextMessage,
  createLocalSession,
  getHealth,
  getLocalSession,
  getLocalTranscript,
  getLocalVoiceStatus,
  setLocalSessionMode,
  synthesizeLocalAssistantMessage,
} from '../api/server';
import { hydrateSessionStoreFromPersistence, resetSessionStore } from '../sessions/sessionStore';
import { DEFAULT_SESSION_MODE } from '../../../../shared/contracts/modes';

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


  it('synthesizes assistant transcript text via standard voice provider without creating new transcript messages', () => {
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
    const output = synthesizeLocalAssistantMessage(session.session_id, assistantMessage.id);
    const afterCount = getLocalTranscript(session.session_id).length;

    expect(output.spoken_text).toBe('Speak the canonical assistant transcript.');
    expect(output.provider_id).toBe('local_fallback_tts');
    expect(afterCount).toBe(beforeCount);
  });


  it('reports standard voice active by default with no cloned voice profile configured', () => {
    const status = getLocalVoiceStatus();

    expect(status.active_provider_id).toBe('local_fallback_tts');
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
    storage.setItem('askchappy.local.session_store.v1', '{bad-json');

    expect(() => hydrateSessionStoreFromPersistence()).not.toThrow();
    expect(storage.getItem('askchappy.local.session_store.v1')).toBeNull();
  });
});
