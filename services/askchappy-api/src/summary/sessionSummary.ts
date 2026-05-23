import { isSessionMode, type SessionMode } from '../../../../shared/contracts/modes';
import type { AskChappySession } from '../sessions/sessionStore';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import type { ModeChangeEventMeta } from '../events/sessionEvents';

export type SessionSummary = {
  sessionOverview: string;
  finalMode: SessionMode;
  modeHistory: string[];
  keyDiscussionNotes: string[];
  actionItems: string[];
  talkTrack: string;
  followUpDraft: string;
  hasEnoughContextForFollowUp: boolean;
  needsMoreTranscriptContext: boolean;
};

const MINIMUM_FOLLOW_UP_TEXT_LENGTH = 12;

const isActionLike = (text: string): boolean => /\b(next step|follow up|send|share|review|schedule|prepare|draft|plan|deliver)\b/i.test(text);

const getUserMessages = (transcript: TranscriptMessage[]): TranscriptMessage[] => transcript.filter((message) => message.role === 'user');

const isModeChangeEventMeta = (meta: Record<string, unknown>): meta is ModeChangeEventMeta =>
  isSessionMode(meta.from_mode) && isSessionMode(meta.to_mode) && ['user', 'assistant', 'system'].includes(String(meta.actor));

const toModeHistoryLine = (meta: ModeChangeEventMeta, timestamp: string): string => {
  const readableTs = new Date(timestamp).toLocaleString('en-US', { timeZone: 'UTC', hour12: false });
  return `${readableTs} UTC — ${meta.from_mode} → ${meta.to_mode} (${meta.actor})`;
};

export const generateSessionSummary = (session: AskChappySession): SessionSummary => {
  const userMessages = getUserMessages(session.transcript);
  const meaningfulUserMessages = userMessages.filter((message) => message.text.trim().length >= MINIMUM_FOLLOW_UP_TEXT_LENGTH);

  const modeChangeEvents = session.events
    .filter((event) => event.event_type === 'mode_change')
    .map((event) => ({ meta: event.meta, ts: event.ts }));

  const modeHistoryLines = modeChangeEvents.flatMap((event) =>
    isModeChangeEventMeta(event.meta)
      ? [toModeHistoryLine(event.meta, event.ts)]
      : [`${new Date(event.ts).toLocaleString('en-US', { timeZone: 'UTC', hour12: false })} UTC — mode change recorded (details unavailable)`],
  );

  const modeHistory =
    modeHistoryLines.length === 0
      ? ['No mode changes recorded. Session stayed in Open Q&A.']
      : modeHistoryLines;

  const keyDiscussionNotes =
    userMessages.length === 0
      ? ['Not enough transcript context yet. Add user conversation in session to build recap notes.']
      : userMessages.map((message) => message.text.trim()).filter(Boolean);

  const actionItems =
    userMessages.length === 0
      ? ['No explicit action items captured yet.']
      : userMessages
          .filter((message) => isActionLike(message.text))
          .map((message) => `Action from transcript: ${message.text.trim()}`);

  if (actionItems.length === 0) {
    actionItems.push('No explicit action items captured yet.');
  }

  const needsMoreTranscriptContext = userMessages.length === 0;

  const talkTrack = needsMoreTranscriptContext
    ? 'Not enough transcript context yet to produce a partner follow-up talk track.'
    : `Use ${session.metadata.askchappy.session_mode} framing to continue the partner conversation with concise next steps from this local-first session.`;

  const hasEnoughContextForFollowUp = meaningfulUserMessages.length > 0;
  const followUpDraft = hasEnoughContextForFollowUp
    ? 'Thanks for the conversation. Based on our AskChappy session, here are the items we discussed and suggested next steps for partner follow-up.'
    : 'More transcript context is needed before generating a follow-up draft placeholder.';

  const sessionOverview =
    session.transcript.length === 0
      ? 'This local-first session has no transcript messages yet.'
      : `This local-first session contains ${session.transcript.length} transcript messages and ${modeChangeEvents.length} mode changes.`;

  return {
    sessionOverview,
    finalMode: session.metadata.askchappy.session_mode,
    modeHistory,
    keyDiscussionNotes,
    actionItems,
    talkTrack,
    followUpDraft,
    hasEnoughContextForFollowUp,
    needsMoreTranscriptContext,
  };
};
