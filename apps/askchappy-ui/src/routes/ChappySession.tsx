import React, { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import type { SessionState } from '../../../../shared/contracts/session';
import type { SessionMode } from '../../../../shared/contracts/modes';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { appendLocalUserTextMessage, generateLocalAssistantMessage, getLocalSession, getLocalTranscript, setLocalSessionMode, getLocalVoiceStatus, synthesizeLocalAssistantMessage, transcribeLocalVoiceInput } from '../../../../services/askchappy-api/src/api/server';
import { ChappyStage } from '../session/ChappyStage';
import { TypedInput } from '../session/TypedInput';
import { TranscriptPanel } from '../transcript/TranscriptPanel';
import { SessionRightRail } from '../session/SessionRightRail';
import { VoiceInput } from '../session/VoiceInput';
import { LocalRuntimeStatus } from '../session/LocalRuntimeStatus';

const STATE_COPY: Record<SessionState, { label: string; description: string }> = {
  ready: { label: 'Ready', description: 'Ask Chappy anything with typed or voice input.' }, listening: { label: 'Listening', description: 'Microphone is recording your voice input.' }, transcribing: { label: 'Transcribing', description: 'Converting voice to canonical transcript text.' }, thinking: { label: 'Thinking', description: 'Chappy is generating the next response.' }, speaking: { label: 'Speaking', description: 'Playing back the latest assistant response.' }, error: { label: 'Needs attention', description: 'A runtime step failed. You can recover and continue.' },
};

export const ChappySession = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [state, setState] = useState<SessionState>('ready');
  const [runtimeNotice, setRuntimeNotice] = useState<string | null>(null);
  const [showModes, setShowModes] = useState(false);
  const [version, setVersion] = useState(0);
  const [voiceNotice, setVoiceNotice] = useState('Ready');
  useEffect(() => {
    document.body.classList.add('session-viewport-lock');
    return () => document.body.classList.remove('session-viewport-lock');
  }, []);
  const session = useMemo(() => (sessionId ? getLocalSession(sessionId) : undefined), [sessionId, version]);
  const voiceStatus = useMemo(() => getLocalVoiceStatus(), []);
  const messages: TranscriptMessage[] = useMemo(() => (!sessionId || !session ? [] : getLocalTranscript(sessionId)), [sessionId, session, version]);
  if (!sessionId || !session) return <main><h1>Session not found</h1><p>Start a local-first Open Q&amp;A session from /chappy.</p></main>;

  const onSubmitText = async (text: string) => { setRuntimeNotice(null); appendLocalUserTextMessage(sessionId, text); setVersion((v) => v + 1); setState('thinking'); const result = await generateLocalAssistantMessage(sessionId); if (!result.ok) { setRuntimeNotice(result.message); setState('error'); return; } setState('ready'); setVersion((v) => v + 1); };
  const onSelectMode = (mode: SessionMode) => { setLocalSessionMode(sessionId, mode, 'user'); setVersion((previous) => previous + 1); };
  const latestAssistant = useMemo(() => [...messages].reverse().find((entry) => entry.role === 'assistant') ?? null, [messages]);
  const onSpeakLatestAssistant = async () => { if (!latestAssistant) return; setState('speaking'); setVoiceNotice('Speaking…'); const tts = await synthesizeLocalAssistantMessage(sessionId, latestAssistant.id); if (tts.audio_status !== 'ready' || !tts.audio_base64 || !tts.audio_format) { setVoiceNotice('Standard local voice is selected, but Kokoro is unavailable. Text response is still available.'); setState('ready'); return; } const audio = new Audio(`data:audio/${tts.audio_format};base64,${tts.audio_base64}`); await audio.play(); setState('ready'); setVoiceNotice('Ready'); };
  const onTranscribeVoice = async (blob: Blob) => { setState('transcribing'); setVoiceNotice('Transcribing…'); const stt = await transcribeLocalVoiceInput(sessionId, blob); if (!stt.ok) { setState('ready'); setVoiceNotice(stt.code === 'no_speech' ? 'No speech detected' : stt.code === 'not_configured' ? 'Mic unavailable' : stt.message); return; } setVersion((v) => v + 1); setState('thinking'); const result = await generateLocalAssistantMessage(sessionId); if (!result.ok) { setRuntimeNotice(result.message); setState('error'); return; } setVersion((v) => v + 1); setState('ready'); setVoiceNotice('Ready'); };
  const stateUi = STATE_COPY[state];

  return (
    <main className="meeting-room session-shell" aria-label="askchappy session room">
      <header className="card status-bar top-meeting-bar" aria-label="top meeting bar">
        <h1>AskChappy</h1>
        <p>Open Q&amp;A • {stateUi.label} • session {sessionId}</p>
      </header>
      <div className="meeting-content" aria-label="meeting body">
        <section className="meeting-stage" aria-label="meeting stage">
          <ChappyStage state={state} />
        </section>
        <section className="meeting-side-column" aria-label="meeting side column">
          <TranscriptPanel messages={messages} />
          <SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={onSelectMode} compact />
        </section>
      </div>
      {showModes ? (
        <div className="modes-overlay card panel" role="dialog" aria-label="guided modes overlay">
          <button className="btn secondary" type="button" onClick={() => setShowModes(false)}>Close</button>
          <SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={(mode) => { onSelectMode(mode); setShowModes(false); }} />
        </div>
      ) : null}
      <section className="meeting-toolbar" aria-label="bottom meeting toolbar">
        <div className="toolbar-notice" role="status">Mic state: {voiceNotice} {runtimeNotice ? `• ${runtimeNotice}` : ''}</div>
        <div className="toolbar-controls">
          <VoiceInput compact onStart={() => { setState('listening'); setVoiceNotice('Listening…'); }} onStop={() => { setState('transcribing'); setVoiceNotice('Transcribing…'); }} onTranscribe={onTranscribeVoice} onError={(message) => { setState('error'); setVoiceNotice(message); }} />
          <TypedInput compact onSubmitText={onSubmitText} />
          <button className="meeting-btn" type="button" onClick={onSpeakLatestAssistant} disabled={!latestAssistant}><span aria-hidden="true">🔊</span> Speak</button>
          <LocalRuntimeStatus compact />
          <button className="meeting-btn" type="button" onClick={() => setShowModes(true)}>Modes</button>
        </div>
        <div className="toolbar-subnotice">
          {!voiceStatus.standard_tts_configured ? <span className="mini-badge">Kokoro unavailable: text-first responses continue.</span> : null}
          <span>Cloned voice status: {voiceStatus.cloned_voice_status_label === 'Not configured' ? 'Cloned voice not configured.' : `${voiceStatus.cloned_voice_status_label}.`}</span>
        </div>
      </section>
    </main>
  );
};
