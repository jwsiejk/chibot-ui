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
  synthesizeLocalAssistantMessage,
} from '../../../../services/askchappy-api/src/api/server';
import { ChappyStage } from '../session/ChappyStage';
import { TypedInput } from '../session/TypedInput';
import { TranscriptPanel } from '../transcript/TranscriptPanel';
import { SessionRightRail } from '../session/SessionRightRail';
import { VoiceInput } from '../session/VoiceInput';
import { LocalRuntimeStatus } from '../session/LocalRuntimeStatus';
import { transcribeLocalVoiceInput } from '../../../../services/askchappy-api/src/api/server';

const STATE_COPY: Record<SessionState, { label: string; description: string }> = {
  ready: { label: 'Ready', description: 'Ask Chappy anything with typed or voice input.' },
  listening: { label: 'Listening', description: 'Microphone is recording your voice input.' },
  transcribing: { label: 'Transcribing', description: 'Converting voice to canonical transcript text.' },
  thinking: { label: 'Thinking', description: 'Chappy is generating the next response.' },
  speaking: { label: 'Speaking', description: 'Playing back the latest assistant response.' },
  error: { label: 'Needs attention', description: 'A runtime step failed. You can recover and continue.' },
};

export const ChappySession = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [state, setState] = useState<SessionState>('ready');
  const [runtimeNotice, setRuntimeNotice] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [voiceNotice, setVoiceNotice] = useState('Ready to record.');

  const session = useMemo(() => (sessionId ? getLocalSession(sessionId) : undefined), [sessionId, version]);
  const voiceStatus = useMemo(() => getLocalVoiceStatus(), []);
  const messages: TranscriptMessage[] = useMemo(() => {
    if (!sessionId || !session) return [];
    return getLocalTranscript(sessionId);
  }, [sessionId, session, version]);

  if (!sessionId || !session) return <main><h1>Session not found</h1><p>Start a local-first Open Q&amp;A session from /chappy.</p></main>;

  const onSubmitText = async (text: string) => {
    setRuntimeNotice(null);
    appendLocalUserTextMessage(sessionId, text);
    setVersion((v) => v + 1);
    setState('thinking');
    const result = await generateLocalAssistantMessage(sessionId);
    if (!result.ok) {
      setRuntimeNotice(result.message);
      setState('error');
      return;
    }
    setState('ready');
    setVersion((v) => v + 1);
  };

  const onSelectMode = (mode: SessionMode) => {
    setLocalSessionMode(sessionId, mode, 'user');
    setVersion((previous) => previous + 1);
  };

  const latestAssistant = useMemo(() => [...messages].reverse().find((entry) => entry.role === 'assistant') ?? null, [messages]);

  const onSpeakLatestAssistant = async () => {
    if (!latestAssistant) return;
    setState('speaking');
    setVoiceNotice('Speaking response.');
    const tts = await synthesizeLocalAssistantMessage(sessionId, latestAssistant.id);
    if (tts.audio_status !== 'ready' || !tts.audio_base64 || !tts.audio_format) {
      setVoiceNotice('Standard local voice is selected, but Kokoro is unavailable. Text response is still available.');
      setState('ready');
      return;
    }
    const audio = new Audio(`data:audio/${tts.audio_format};base64,${tts.audio_base64}`);
    await audio.play();
    setState('ready');
    setVoiceNotice('Ready to record.');
  };

  const onTranscribeVoice = async (blob: Blob) => {
    setState('transcribing');
    setVoiceNotice('Transcribing voice input.');
    const stt = await transcribeLocalVoiceInput(sessionId, blob);
    if (!stt.ok) {
      setState('ready');
      if (stt.code === 'no_speech') {
        setVoiceNotice('No speech detected. Try again and speak clearly.');
      } else {
        setVoiceNotice(stt.code === 'not_configured' ? 'Local STT not configured.' : stt.message);
      }
      return;
    }
    setVersion((v) => v + 1);
    setState('thinking');
    const result = await generateLocalAssistantMessage(sessionId);
    if (!result.ok) {
      setRuntimeNotice(result.message);
      setState('error');
      return;
    }
    setVersion((v) => v + 1);
    setState('ready');
    setVoiceNotice('Ready to record.');
  };

  const stateUi = STATE_COPY[state];

  return (
    <main>
      <h1>AskChappy session</h1>
      <p>Local production working session ID: {sessionId}</p>
      <section aria-label="session status">
        <h2>Session status: {stateUi.label}</h2>
        <p>{stateUi.description}</p>
        <p>Voice input: {voiceNotice}</p>
        {!voiceStatus.standard_tts_configured ? <p>Standard local voice is selected. Kokoro is not configured/reachable, so transcript responses stay text-first.</p> : null}
        {runtimeNotice ? <p>{runtimeNotice}</p> : null}
      </section>
      <LocalRuntimeStatus />
      <button type="button" onClick={onSpeakLatestAssistant} disabled={!latestAssistant}>Speak response</button>
      <ChappyStage state={state} />
      <TranscriptPanel messages={messages} />
      <TypedInput onSubmitText={onSubmitText} />
      <VoiceInput
        onStart={() => {
          setState('listening');
          setVoiceNotice('Recording. Press stop when done.');
        }}
        onStop={() => {
          setState('transcribing');
          setVoiceNotice('Transcribing voice input.');
        }}
        onTranscribe={onTranscribeVoice}
        onError={(message) => {
          setState('error');
          setVoiceNotice(message);
        }}
      />
      <p>Cloned voice status: {voiceStatus.cloned_voice_status_label === 'Not configured' ? 'Cloned voice not configured.' : `${voiceStatus.cloned_voice_status_label}.`}</p>
      <SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={onSelectMode} />
    </main>
  );
};
