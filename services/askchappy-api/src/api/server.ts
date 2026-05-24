import { appendUserTextMessage, appendUserVoiceMessage } from '../transcript/transcriptEngine';
import {
  appendTranscriptMessage,
  createSession,
  getSession,
  listTranscript,
  type AskChappySession,
  updateSessionMode,
} from '../sessions/sessionStore';
import type { SessionMode } from '../../../../shared/contracts/modes';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { getLocalVoiceRuntimeStatus, synthesizeAssistantTranscriptMessage } from '../voice/voiceRuntime';
import { generateAssistantResponse } from '../assistant/ollamaAdapter';
import { transcribeWithFasterWhisper } from '../voice/stt/fasterWhisperAdapter';
import { getLocalRuntimeReadiness } from './localRuntimeReadiness';
import { getLocalGpuValidationStatus } from './localGpuValidation';

export type ApiHealth = { service: 'askchappy-api'; status: 'placeholder' };

export function getHealth(): ApiHealth {
  return { service: 'askchappy-api', status: 'placeholder' };
}

export const createLocalSession = (): AskChappySession => createSession();

export const getLocalSession = (sessionId: string): AskChappySession | undefined => getSession(sessionId);

export const appendLocalTranscriptMessage = (
  sessionId: string,
  message: TranscriptMessage,
): TranscriptMessage => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);
  return appendTranscriptMessage(session, message);
};

export const appendLocalUserTextMessage = (sessionId: string, text: string): TranscriptMessage => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);

  const userMessage = appendUserTextMessage(session, text);
  return appendTranscriptMessage(session, userMessage);
};

export const appendLocalUserVoiceMessage = (sessionId: string, text: string, meta: Record<string, unknown>): TranscriptMessage => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);
  const userMessage = appendUserVoiceMessage(session, text, meta);
  return appendTranscriptMessage(session, userMessage);
};

export const transcribeLocalVoiceInput = async (sessionId: string, audioBlob: Blob) => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);

  const stt = await transcribeWithFasterWhisper(audioBlob);
  if (!stt.ok) return stt;

  const message = appendLocalUserVoiceMessage(sessionId, stt.text, { stt: stt.provider });
  return { ok: true as const, message, provider: stt.provider };
};

export const getLocalTranscript = (sessionId: string): TranscriptMessage[] => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);
  return listTranscript(session);
};


export const setLocalSessionMode = (
  sessionId: string,
  toMode: SessionMode,
  actor: 'user' | 'assistant' | 'system' = 'user',
): AskChappySession => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);
  return updateSessionMode(session, toMode, actor);
};


export const generateLocalAssistantMessage = async (sessionId: string) => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);

  const latestUser = [...session.transcript].reverse().find((entry) => entry.role === 'user');
  if (!latestUser) throw new Error('Cannot generate assistant response without a user transcript message.');

  const result = await generateAssistantResponse({
    session_id: session.session_id,
    metadata: session.metadata,
    transcript: session.transcript,
  });

  if (!result.ok) return result;

  const assistantMessage: TranscriptMessage = {
    id: `msg_${crypto.randomUUID()}`,
    ts: new Date().toISOString(),
    role: 'assistant',
    text: result.text,
    source: 'assistant_stream',
    session_id: session.session_id,
    meta: { runtime: result.runtime },
  };

  appendTranscriptMessage(session, assistantMessage);
  return result;
};
export const synthesizeLocalAssistantMessage = async (sessionId: string, messageId: string) => {
  const session = getSession(sessionId);
  if (!session) throw new Error(`Session not found: ${sessionId}`);
  const message = session.transcript.find((entry) => entry.id === messageId);
  if (!message) throw new Error(`Transcript message not found: ${messageId}`);
  return synthesizeAssistantTranscriptMessage({ session_id: sessionId, message });
};

export const getLocalVoiceStatus = () => getLocalVoiceRuntimeStatus();

export const getLocalRuntimeReadinessStatus = () => getLocalRuntimeReadiness();


export const getLocalGpuValidationReport = () => getLocalGpuValidationStatus();
