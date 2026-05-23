import { isTranscriptMessage, type TranscriptMessage } from '../../../../shared/contracts/transcript';
import type { AskChappySession } from '../sessions/sessionStore';

const assertTranscriptMessage = (message: unknown): asserts message is TranscriptMessage => {
  if (!isTranscriptMessage(message)) {
    throw new Error('Invalid transcript message: must match canonical TranscriptMessage contract.');
  }
};

export const appendTranscriptMessageToSession = (
  session: AskChappySession,
  message: TranscriptMessage,
): TranscriptMessage => {
  assertTranscriptMessage(message);

  if (message.session_id !== session.session_id) {
    throw new Error('Invalid transcript message: session_id does not match session.');
  }

  session.transcript.push(message);
  return message;
};

export const appendUserTextMessage = (session: AskChappySession, text: string): TranscriptMessage => ({
  id: `msg_${crypto.randomUUID()}`,
  ts: new Date().toISOString(),
  role: 'user',
  text,
  source: 'typed',
  session_id: session.session_id,
  meta: {},
});
