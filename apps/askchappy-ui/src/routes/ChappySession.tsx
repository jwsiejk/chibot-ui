import React, { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import type { SessionState } from '../../../../shared/contracts/session';
import type { SessionMode } from '../../../../shared/contracts/modes';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import {
  appendLocalUserTextMessage,
  generateLocalAssistantMessage,
  getLocalSession,
  getLocalTranscript,
  setLocalSessionMode,
  getLocalVoiceStatus,
} from '../../../../services/askchappy-api/src/api/server';
import { ChappyStage } from '../session/ChappyStage';
import { TypedInput } from '../session/TypedInput';
import { TranscriptPanel } from '../transcript/TranscriptPanel';
import { SessionRightRail } from '../session/SessionRightRail';

export const ChappySession = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [state, setState] = useState<SessionState>('ready');
  const [runtimeNotice, setRuntimeNotice] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const session = useMemo(() => (sessionId ? getLocalSession(sessionId) : undefined), [sessionId, version]);
  const voiceStatus = useMemo(() => getLocalVoiceStatus(), []);
  const messages: TranscriptMessage[] = useMemo(() => {
    if (!sessionId || !session) return [];
    return getLocalTranscript(sessionId);
  }, [sessionId, session, version]);

  if (!sessionId || !session) {
    return (
      <main>
        <h1>Session not found</h1>
        <p>Start a local-first Open Q&amp;A session from /chappy.</p>
      </main>
    );
  }

  const onSubmitText = async (text: string) => {
    setRuntimeNotice(null);
    appendLocalUserTextMessage(sessionId, text);
    setVersion((previous) => previous + 1);
    setState('thinking');
    const result = await generateLocalAssistantMessage(sessionId);
    if (!result.ok) {
      setRuntimeNotice(result.message);
      setState('error');
      setVersion((previous) => previous + 1);
      return;
    }

    setState('ready');
    setVersion((previous) => previous + 1);
  };

  const onSelectMode = (mode: SessionMode) => {
    setLocalSessionMode(sessionId, mode, 'user');
    setVersion((previous) => previous + 1);
  };

  return (
    <main>
      <h1>AskChappy session</h1>
      <p>Local production working session ID: {sessionId}</p>
      <p>Session state indicator: {state}</p>
      <p>Speech provider status: Standard voice active.</p>
      <p>Cloned voice status: {voiceStatus.cloned_voice_status_label === 'Not configured' ? 'Cloned voice not configured' : voiceStatus.cloned_voice_status_label}.</p>
      <ChappyStage state={state} />
      <TranscriptPanel messages={messages} />
      {runtimeNotice ? <p>{runtimeNotice}</p> : null}
      <TypedInput onSubmitText={onSubmitText} />
      <SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={onSelectMode} />
    </main>
  );
};
