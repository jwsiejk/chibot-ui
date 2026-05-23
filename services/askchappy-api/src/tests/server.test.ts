import { describe, expect, it, beforeEach } from 'vitest';
import {
  appendLocalTranscriptMessage,
  appendLocalUserTextMessage,
  createLocalSession,
  getHealth,
  getLocalSession,
  getLocalTranscript,
} from '../api/server';
import { resetSessionStore } from '../sessions/sessionStore';
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
});
