import { DEFAULT_METADATA, type AskChappyMetadata } from '../../../../shared/contracts/session';
import { createSessionEvent, type SessionEvent } from '../events/sessionEvents';
import { appendTranscriptMessageToSession } from '../transcript/transcriptEngine';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';

export type AskChappySession = {
  session_id: string;
  created_at: string;
  updated_at: string;
  metadata: AskChappyMetadata;
  transcript: TranscriptMessage[];
  events: SessionEvent[];
};

const cloneDefaultMetadata = (): AskChappyMetadata => ({
  askchappy: {
    ...DEFAULT_METADATA.askchappy,
    context: { ...DEFAULT_METADATA.askchappy.context },
  },
});

const sessions = new Map<string, AskChappySession>();

const touchSession = (session: AskChappySession, ts: string): AskChappySession => {
  session.updated_at = ts;
  return session;
};

export const appendSessionEvent = (
  session: AskChappySession,
  eventType: 'session_created' | 'transcript_message_appended',
  meta: Record<string, unknown> = {},
  ts = new Date().toISOString(),
): SessionEvent => {
  const event = createSessionEvent({ session_id: session.session_id, event_type: eventType, meta, ts });
  session.events.push(event);
  touchSession(session, ts);
  return event;
};

export const createSession = (): AskChappySession => {
  const now = new Date().toISOString();
  const session: AskChappySession = {
    session_id: `session_${crypto.randomUUID()}`,
    created_at: now,
    updated_at: now,
    metadata: cloneDefaultMetadata(),
    transcript: [],
    events: [],
  };

  appendSessionEvent(session, 'session_created', {}, now);
  sessions.set(session.session_id, session);
  return session;
};

export const getSession = (sessionId: string): AskChappySession | undefined => sessions.get(sessionId);

export const appendTranscriptMessage = (
  session: AskChappySession,
  message: TranscriptMessage,
): TranscriptMessage => {
  const appendedMessage = appendTranscriptMessageToSession(session, message);
  appendSessionEvent(session, 'transcript_message_appended', { message_id: appendedMessage.id }, appendedMessage.ts);
  return appendedMessage;
};

export const listTranscript = (session: AskChappySession): TranscriptMessage[] => session.transcript;

export const resetSessionStore = (): void => {
  sessions.clear();
};
