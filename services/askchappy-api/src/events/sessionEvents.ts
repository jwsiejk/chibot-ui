import type { SessionMode } from '../../../../shared/contracts/modes';

export const SESSION_EVENT_TYPES = ['session_created', 'transcript_message_appended', 'mode_change'] as const;

export type SessionEventType = (typeof SESSION_EVENT_TYPES)[number];

export type SessionEvent = {
  id: string;
  ts: string;
  session_id: string;
  event_type: SessionEventType;
  meta: Record<string, unknown>;
};

export type ModeChangeEventMeta = {
  from_mode: SessionMode;
  to_mode: SessionMode;
  actor: 'user' | 'assistant' | 'system';
};

export const isSessionEventType = (value: unknown): value is SessionEventType =>
  typeof value === 'string' && SESSION_EVENT_TYPES.includes(value as SessionEventType);

export const createSessionEvent = (input: {
  session_id: string;
  event_type: SessionEventType;
  ts?: string;
  meta?: Record<string, unknown>;
}): SessionEvent => ({
  id: `event_${crypto.randomUUID()}`,
  ts: input.ts ?? new Date().toISOString(),
  session_id: input.session_id,
  event_type: input.event_type,
  meta: input.meta ?? {},
});
