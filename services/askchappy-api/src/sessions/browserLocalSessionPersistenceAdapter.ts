import { isAskChappyMetadata } from '../../../../shared/contracts/session';
import { isTranscriptMessage } from '../../../../shared/contracts/transcript';
import { isSessionEventType, type SessionEvent } from '../events/sessionEvents';
import type { AskChappySession } from './sessionStore';

// Browser-local, local-first persistence adapter only.
// This module intentionally uses window.localStorage and is not backend/database/cloud persistence.
export const BROWSER_LOCAL_SESSION_STORAGE_KEY = 'askchappy.local.session_store.v1';
export const BROWSER_LOCAL_SESSION_SCHEMA_VERSION = 1;

type PersistedPayload = {
  schema_version: number;
  sessions: AskChappySession[];
};

const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object';

const isSessionEvent = (value: unknown): value is SessionEvent => {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'string' &&
    typeof value.ts === 'string' &&
    typeof value.session_id === 'string' &&
    isSessionEventType(value.event_type) &&
    isRecord(value.meta)
  );
};

const isAskChappySession = (value: unknown): value is AskChappySession => {
  if (!isRecord(value)) return false;
  return (
    typeof value.session_id === 'string' &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    isAskChappyMetadata(value.metadata) &&
    Array.isArray(value.transcript) &&
    value.transcript.every((entry) => isTranscriptMessage(entry) && entry.session_id === value.session_id) &&
    Array.isArray(value.events) &&
    value.events.every((event) => isSessionEvent(event) && event.session_id === value.session_id)
  );
};

const getStorage = (): Storage | null => {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
};

export const loadPersistedSessions = (): {
  sessions: Map<string, AskChappySession>;
  recovered_from_malformed_payload: boolean;
} => {
  const storage = getStorage();
  if (!storage) return { sessions: new Map(), recovered_from_malformed_payload: false };

  const raw = storage.getItem(BROWSER_LOCAL_SESSION_STORAGE_KEY);
  if (!raw) return { sessions: new Map(), recovered_from_malformed_payload: false };

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (
      !isRecord(parsed) ||
      parsed.schema_version !== BROWSER_LOCAL_SESSION_SCHEMA_VERSION ||
      !Array.isArray(parsed.sessions)
    ) {
      storage.removeItem(BROWSER_LOCAL_SESSION_STORAGE_KEY);
      return { sessions: new Map(), recovered_from_malformed_payload: true };
    }

    const sessions = parsed.sessions.filter(isAskChappySession);
    if (sessions.length !== parsed.sessions.length) {
      storage.removeItem(BROWSER_LOCAL_SESSION_STORAGE_KEY);
      return { sessions: new Map(), recovered_from_malformed_payload: true };
    }

    return {
      sessions: new Map(sessions.map((session) => [session.session_id, session])),
      recovered_from_malformed_payload: false,
    };
  } catch {
    storage.removeItem(BROWSER_LOCAL_SESSION_STORAGE_KEY);
    return { sessions: new Map(), recovered_from_malformed_payload: true };
  }
};

export const persistSessions = (sessions: Map<string, AskChappySession>): void => {
  const storage = getStorage();
  if (!storage) return;
  const payload: PersistedPayload = {
    schema_version: BROWSER_LOCAL_SESSION_SCHEMA_VERSION,
    sessions: Array.from(sessions.values()),
  };
  storage.setItem(BROWSER_LOCAL_SESSION_STORAGE_KEY, JSON.stringify(payload));
};

export const clearPersistedSessions = (): void => {
  const storage = getStorage();
  storage?.removeItem(BROWSER_LOCAL_SESSION_STORAGE_KEY);
};
