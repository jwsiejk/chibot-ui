import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import type { SessionState } from '../../../../shared/contracts/session';
import type { SessionMode } from '../../../../shared/contracts/modes';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { appendLocalUserTextMessage, generateLocalAssistantMessage, getLocalSession, getLocalTranscript, getLocalVoiceStatus, setLocalSessionMode, synthesizeLocalAssistantMessage, transcribeLocalVoiceInput } from '../../../../services/askchappy-api/src/api/server';
import { useAuth } from '../auth/authState';
import { ChappyStage } from '../session/ChappyStage';
import { TypedInput } from '../session/TypedInput';
import { TranscriptPanel } from '../transcript/TranscriptPanel';
import { SessionRightRail } from '../session/SessionRightRail';
import { VoiceInput } from '../session/VoiceInput';
import { LocalRuntimeStatus } from '../session/LocalRuntimeStatus';
import { AdminRuntimeConsoleModal, type ClientDiagnosticEvent } from '../session/AdminRuntimeConsoleModal';

const STATE_COPY: Record<SessionState, string> = { ready: 'Ready', listening: 'Listening', transcribing: 'Transcribing', thinking: 'Thinking', speaking: 'Speaking', error: 'Needs attention' };

export const ChappySession = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { user } = useAuth();
  const [state, setState] = useState<SessionState>('ready');
  const [runtimeNotice, setRuntimeNotice] = useState<string | null>(null);
  const [showModes, setShowModes] = useState(false);
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [version, setVersion] = useState(0);
  const [voiceNotice, setVoiceNotice] = useState('Ready');
  const [chappyMuted, setChappyMuted] = useState(false);
  const [diagnostics, setDiagnostics] = useState<ClientDiagnosticEvent[]>([]);
  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  const pushDiagnostic = (event: string) => setDiagnostics((prev) => [{ id: crypto.randomUUID(), ts: new Date().toISOString(), event }, ...prev].slice(0, 25));

  useEffect(() => {
    document.body.classList.add('session-viewport-lock');
    pushDiagnostic('session started');
    return () => document.body.classList.remove('session-viewport-lock');
  }, []);

  useEffect(() => () => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }
  }, []);

  const session = useMemo(() => (sessionId ? getLocalSession(sessionId) : undefined), [sessionId, version]);
  const voiceStatus = useMemo(() => getLocalVoiceStatus(), []);
  const messages: TranscriptMessage[] = useMemo(() => (!sessionId || !session ? [] : getLocalTranscript(sessionId)), [sessionId, session, version]);
  if (!sessionId || !session) return <main><h1>Session not found</h1><p>Start a local-first Open Q&amp;A session from /chappy.</p></main>;

  const speakAssistant = async (messageId: string) => {
    if (chappyMuted) {
      setVoiceNotice('Chappy voice muted. Transcript only.');
      return;
    }
    pushDiagnostic('tts request started');
    setState('speaking');
    setVoiceNotice('Chappy speaking…');
    const tts = await synthesizeLocalAssistantMessage(sessionId, messageId);
    if (tts.audio_status !== 'ready' || !tts.audio_base64 || !tts.audio_format) {
      if (tts.unavailable_reason === 'request_cancelled') pushDiagnostic('tts request cancelled');
      else if (tts.unavailable_reason === 'invalid_response') pushDiagnostic('tts invalid response');
      else pushDiagnostic('tts synthesis failed');
      setVoiceNotice(tts.unavailable_message ?? 'Chappy voice unavailable. Transcript response is still available.');
      setState('ready');
      return;
    }
    pushDiagnostic('tts ready');
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }
    const audio = new Audio(`data:audio/${tts.audio_format};base64,${tts.audio_base64}`);
    activeAudioRef.current = audio;
    try {
      await audio.play();
      pushDiagnostic('audio playback started');
    } catch (error) {
      const blocked = error instanceof DOMException && error.name === 'NotAllowedError';
      pushDiagnostic(blocked ? 'audio playback blocked' : 'audio playback failed');
      setVoiceNotice('Browser blocked or failed Chappy voice playback. Click in the room or unmute Chappy to enable audio.');
      setState('ready');
      return;
    }
    setState('ready');
    setVoiceNotice('Chappy voice on');
  };

  const runAssistantTurn = async () => {
    setState('thinking');
    const result = await generateLocalAssistantMessage(sessionId);
    if (!result.ok) {
      pushDiagnostic('assistant generation failure');
      setRuntimeNotice(result.message);
      setState('error');
      return;
    }
    setVersion((v) => v + 1);
    const refreshed = getLocalTranscript(sessionId);
    const newestAssistant = [...refreshed].reverse().find((entry) => entry.role === 'assistant');
    setState('ready');
    if (newestAssistant) await speakAssistant(newestAssistant.id);
  };

  const onSubmitText = async (text: string) => {
    setRuntimeNotice(null);
    appendLocalUserTextMessage(sessionId, text);
    pushDiagnostic('typed message submitted');
    setVersion((v) => v + 1);
    await runAssistantTurn();
  };

  const onSelectMode = (mode: SessionMode) => { setLocalSessionMode(sessionId, mode, 'user'); setVersion((previous) => previous + 1); };
  const onTranscribeVoice = async (blob: Blob) => {
    setState('transcribing');
    setVoiceNotice('Transcribing…');
    pushDiagnostic('mic transcribing started');
    const stt = await transcribeLocalVoiceInput(sessionId, blob);
    if (!stt.ok) {
      if (stt.code === 'no_speech') pushDiagnostic('STT no speech');
      else pushDiagnostic('STT failure');
      setState('ready');
      setVoiceNotice(stt.code === 'no_speech' ? 'No speech detected' : stt.code === 'not_configured' ? 'Mic unavailable' : stt.message);
      return;
    }
    setVersion((v) => v + 1);
    await runAssistantTurn();
  };

  return (
    <main className="meeting-room session-shell" aria-label="askchappy session room">
      <header className="card status-bar top-meeting-bar" aria-label="top meeting bar">
        <h1>AskChappy</h1>
        <p>Open Q&amp;A • {STATE_COPY[state]} • session {sessionId}{user?.role === 'admin' ? ' • Admin' : ''}</p>
      </header>
      <div className="meeting-content" aria-label="meeting body">
        <section className="meeting-stage" aria-label="meeting stage"><ChappyStage state={state} /></section>
        <section className="meeting-side-column" aria-label="meeting side column">
          <TranscriptPanel messages={messages} />
          <SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={onSelectMode} compact />
        </section>
      </div>
      {showModes ? <div className="modes-overlay card panel" role="dialog" aria-label="guided modes overlay"><button className="btn secondary" type="button" onClick={() => setShowModes(false)}>Close</button><SessionRightRail activeMode={session.metadata.askchappy.session_mode} onSelectMode={(mode) => { onSelectMode(mode); setShowModes(false); }} /></div> : null}
      <section className="meeting-toolbar" aria-label="bottom meeting toolbar">
        <div className="toolbar-notice" role="status">Mic state: {voiceNotice} {runtimeNotice ? `• ${runtimeNotice}` : ''}</div>
        <div className="toolbar-controls">
          <VoiceInput compact onStart={() => { setState('listening'); setVoiceNotice('Listening…'); pushDiagnostic('mic listening started'); }} onStop={() => { setState('transcribing'); setVoiceNotice('Transcribing…'); }} onTranscribe={onTranscribeVoice} onError={(message) => { setState('error'); setVoiceNotice(message); }} />
          <TypedInput compact onSubmitText={onSubmitText} />
          <button className="meeting-btn" type="button" onClick={() => { setChappyMuted((m) => !m); setVoiceNotice(chappyMuted ? 'Chappy voice on' : 'Chappy voice muted. Transcript only.'); pushDiagnostic(chappyMuted ? 'Chappy unmuted' : 'Chappy muted'); }}>{chappyMuted ? 'Unmute Chappy' : 'Mute Chappy'}</button>
          <LocalRuntimeStatus compact />
          <button className="meeting-btn" type="button" onClick={() => setShowModes(true)}>Modes</button>
          {user?.role === 'admin' ? <button className="meeting-btn" type="button" onClick={() => { setShowAdminModal(true); pushDiagnostic('admin modal opened'); }}>Admin</button> : null}
        </div>
        <div className="toolbar-subnotice">
          {!voiceStatus.standard_tts_configured ? <span className="mini-badge">Chappy voice unavailable. Transcript response is still available.</span> : null}
          <span>Cloned voice status: {voiceStatus.cloned_voice_status_label === 'Not configured' ? 'Cloned voice not configured.' : `${voiceStatus.cloned_voice_status_label}.`}</span>
        </div>
      </section>
      <AdminRuntimeConsoleModal isOpen={showAdminModal && user?.role === 'admin'} onClose={() => setShowAdminModal(false)} browserMicStatus={voiceNotice.includes('Permission denied') ? 'permission_denied' : 'available_or_unknown'} diagnostics={diagnostics} />
    </main>
  );
};
