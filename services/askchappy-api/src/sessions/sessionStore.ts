import { assertSessionMode, type SessionMode } from '../../../../shared/contracts/modes';
import { DEFAULT_METADATA, type AskChappyMetadata } from '../../../../shared/contracts/session';
import { createSessionEvent, type SessionEvent } from '../events/sessionEvents';
import { appendTranscriptMessageToSession } from '../transcript/transcriptEngine';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { loadPersistedSessions, persistSessions, clearPersistedSessions } from './browserLocalSessionPersistenceAdapter';
import { CREATE_PRESENTATIONS_INTRO_MESSAGE, createPresentationsModeState } from '../../../../shared/contracts/createPresentationsMode';

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

const { sessions, recovered_from_malformed_payload } = loadPersistedSessions();

const touchSession = (session: AskChappySession, ts: string): AskChappySession => {
  session.updated_at = ts;
  persistSessions(sessions);
  return session;
};

export const appendSessionEvent = (
  session: AskChappySession,
  eventType: 'session_created' | 'transcript_message_appended' | 'mode_change',
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
  persistSessions(sessions);
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

export const updateSessionMode = (
  session: AskChappySession,
  toMode: SessionMode,
  actor: 'user' | 'assistant' | 'system',
): AskChappySession => {
  assertSessionMode(toMode);
  const fromMode = session.metadata.askchappy.session_mode;
  if (fromMode === toMode) return session;

  const ts = new Date().toISOString();
  session.metadata.askchappy.session_mode = toMode;
  if (toMode === 'create_presentations') {
    session.metadata.askchappy.create_presentations_state = createPresentationsModeState();
    const assistantMessage: TranscriptMessage = {
      id: `msg_${crypto.randomUUID()}`,
      ts,
      role: 'assistant',
      text: CREATE_PRESENTATIONS_INTRO_MESSAGE,
      source: 'system',
      session_id: session.session_id,
      meta: { mode: 'create_presentations', step: 'intro' },
    };
    appendTranscriptMessageToSession(session, assistantMessage);
  } else if (fromMode === 'create_presentations') {
    session.metadata.askchappy.create_presentations_state = null;
  }
  appendSessionEvent(session, 'mode_change', { from_mode: fromMode, to_mode: toMode, actor }, ts);
  touchSession(session, ts);
  return session;
};

export const listTranscript = (session: AskChappySession): TranscriptMessage[] => session.transcript;

export const resetSessionStore = (): void => {
  sessions.clear();
  clearPersistedSessions();
};

export const hydrateSessionStoreFromPersistence = (): void => {
  const loaded = loadPersistedSessions();
  sessions.clear();
  loaded.sessions.forEach((session, sessionId) => sessions.set(sessionId, session));
};

if (recovered_from_malformed_payload) {
  const activeSession = sessions.values().next().value as AskChappySession | undefined;
  if (activeSession) {
    appendSessionEvent(activeSession, 'session_created', { persistence_recovery: true });
  }
}
