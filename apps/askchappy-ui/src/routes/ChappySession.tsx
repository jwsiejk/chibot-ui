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
import { AdminRuntimeConsoleModal, type ClientDiagnosticEvent, type TurnLatencyEntry } from '../session/AdminRuntimeConsoleModal';
import { MODE_LOOKUP } from '../modes/guidedModes';

const STATE_COPY: Record<SessionState, string> = { ready: 'Ready', listening: 'Listening', transcribing: 'Transcribing', thinking: 'Thinking', speaking: 'Speaking', error: 'Needs attention' };
const MAX_TURN_LATENCY = 5;

const countWords = (text: string): number => text.trim().split(/\s+/).filter(Boolean).length;

const msBetween = (start?: number, end?: number): number | null => {
  if (typeof start !== 'number' || typeof end !== 'number') return null;
  return Math.max(0, Math.round(end - start));
};

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
  const [turnLatency, setTurnLatency] = useState<TurnLatencyEntry[]>([]);
  const activeAudioRef = useRef<HTMLAudioElement | null>(null);
  const micCaptureStartRef = useRef<number | null>(null);

  const pushDiagnostic = (event: string) => setDiagnostics((prev) => [{ id: crypto.randomUUID(), ts: new Date().toISOString(), event }, ...prev].slice(0, 25));
  const pushTurnLatency = (entry: TurnLatencyEntry) => setTurnLatency((prev) => [entry, ...prev].slice(0, MAX_TURN_LATENCY));

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
  const activeMode = session.metadata.askchappy.session_mode;
  const isCreatePresentationsMode = activeMode === 'create_presentations';

  const speakAssistant = async (
    messageId: string,
    assistantText: string,
    inFlight: Omit<TurnLatencyEntry, 'id' | 'ts' | 'turn_type'> & { turnStartAt: number; turnType: 'typed' | 'voice'; submitAt: number },
  ) => {
    if (chappyMuted) {
      setVoiceNotice('Chappy voice muted. Transcript only.');
      pushTurnLatency({
        id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: inFlight.turnType, ...inFlight,
        tts_ms: null, playback_start_ms: null,
        post_submit_to_text_ready_ms: inFlight.post_submit_to_text_ready_ms ?? msBetween(inFlight.submitAt, performance.now()),
        post_submit_to_chappy_speaking_ms: null,
        total_mic_start_to_text_ready_ms: inFlight.total_mic_start_to_text_ready_ms ?? msBetween(inFlight.turnStartAt, performance.now()),
        total_mic_start_to_chappy_speaking_ms: null,
        tts_skipped_reason: 'muted', playback_skipped_reason: 'muted', failure_stage: null, tts_failed: false,
        assistant_text_chars: assistantText.length,
        assistant_text_words: countWords(assistantText),
      });
      return;
    }
    const ttsStartAt = performance.now();
    pushDiagnostic('tts request started');
    setState('speaking');
    setVoiceNotice('Chappy speaking…');
    const tts = await synthesizeLocalAssistantMessage(sessionId, messageId);
    const ttsEndAt = performance.now();
    const nextInFlight = { ...inFlight, tts_ms: msBetween(ttsStartAt, ttsEndAt) };
    if (tts.audio_status !== 'ready' || !tts.audio_base64 || !tts.audio_format) {
      if (tts.unavailable_reason === 'request_cancelled') pushDiagnostic('tts request cancelled');
      else if (tts.unavailable_reason === 'invalid_response') pushDiagnostic('tts invalid response');
      else pushDiagnostic('tts synthesis failed');
      pushTurnLatency({ id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: inFlight.turnType, ...nextInFlight, playback_start_ms: null, total_ms: null, post_submit_to_chappy_speaking_ms: null, total_mic_start_to_chappy_speaking_ms: null, failure_stage: 'tts', tts_failed: true, assistant_text_chars: assistantText.length, assistant_text_words: countWords(assistantText) });
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
    const playStartAt = performance.now();
    try {
      await audio.play();
      const playSuccessAt = performance.now();
      pushDiagnostic('audio playback started');
      pushTurnLatency({ id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: inFlight.turnType, ...nextInFlight, playback_start_ms: msBetween(playStartAt, playSuccessAt), total_ms: msBetween(inFlight.turnStartAt, playSuccessAt), post_submit_to_chappy_speaking_ms: msBetween(inFlight.submitAt, playSuccessAt), total_mic_start_to_chappy_speaking_ms: msBetween(inFlight.turnStartAt, playSuccessAt), failure_stage: null, tts_failed: false, assistant_text_chars: assistantText.length, assistant_text_words: countWords(assistantText) });
    } catch (error) {
      const blocked = error instanceof DOMException && error.name === 'NotAllowedError';
      pushDiagnostic(blocked ? 'audio playback blocked' : 'audio playback failed');
      pushTurnLatency({ id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: inFlight.turnType, ...nextInFlight, playback_start_ms: null, total_ms: null, post_submit_to_chappy_speaking_ms: null, total_mic_start_to_chappy_speaking_ms: null, failure_stage: 'playback', tts_failed: true, assistant_text_chars: assistantText.length, assistant_text_words: countWords(assistantText) });
      setVoiceNotice('Browser blocked or failed Chappy voice playback. Click in the room or unmute Chappy to enable audio.');
      setState('ready');
      return;
    }
    setState('ready');
    setVoiceNotice('Chappy voice on');
  };

  const runAssistantTurn = async (turnStartAt: number, submitAt: number, turnType: 'typed' | 'voice', micCaptureMs: number | null, sttMs: number | null) => {
    const generationStartAt = performance.now();
    setState('thinking');
    const result = await generateLocalAssistantMessage(sessionId);
    const generationEndAt = performance.now();
    if (!result.ok) {
      pushDiagnostic('assistant generation failure');
      pushTurnLatency({ id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: turnType, mic_capture_ms: micCaptureMs, stt_ms: sttMs, generation_ms: msBetween(generationStartAt, generationEndAt), tts_ms: null, playback_start_ms: null, total_ms: null, assistant_failed: true, failure_stage: 'assistant_generation', time_to_failure_ms: msBetween(turnStartAt, generationEndAt) });
      setRuntimeNotice(result.message);
      setState('error');
      return;
    }
    setVersion((v) => v + 1);
    const refreshed = getLocalTranscript(sessionId);
    const newestAssistant = [...refreshed].reverse().find((entry) => entry.role === 'assistant');
    setState('ready');
    if (newestAssistant) {
      await speakAssistant(newestAssistant.id, newestAssistant.text, {
        turnStartAt,
        turnType,
        mic_capture_ms: micCaptureMs,
        stt_ms: sttMs,
        generation_ms: msBetween(generationStartAt, generationEndAt),
        tts_ms: null,
        post_submit_to_text_ready_ms: msBetween(submitAt, generationEndAt),
        total_mic_start_to_text_ready_ms: msBetween(turnStartAt, generationEndAt),
      });
    }
  };

  const onSubmitText = async (text: string) => {
    const turnStartAt = performance.now();
    setRuntimeNotice(null);
    appendLocalUserTextMessage(sessionId, text);
    pushDiagnostic('typed message submitted');
    setVersion((v) => v + 1);
    await runAssistantTurn(turnStartAt, turnStartAt, 'typed', null, null);
  };

  const onSelectMode = (mode: SessionMode) => { setLocalSessionMode(sessionId, mode, 'user'); setVersion((previous) => previous + 1); };
  const onTranscribeVoice = async (blob: Blob) => {
    const turnStartAt = micCaptureStartRef.current ?? performance.now();
    const micSubmitAt = performance.now();
    const micCaptureMs = msBetween(micCaptureStartRef.current ?? undefined, micSubmitAt);
    const sttStartAt = performance.now();
    setState('transcribing');
    setVoiceNotice('Transcribing…');
    pushDiagnostic('mic transcribing started');
    const stt = await transcribeLocalVoiceInput(sessionId, blob);
    const sttEndAt = performance.now();
    micCaptureStartRef.current = null;
    if (!stt.ok) {
      if (stt.code === 'no_speech') pushDiagnostic('STT no speech');
      else pushDiagnostic('STT failure');
      pushTurnLatency({ id: crypto.randomUUID(), ts: new Date().toISOString(), turn_type: 'voice', mic_capture_ms: micCaptureMs, stt_ms: msBetween(sttStartAt, sttEndAt), generation_ms: null, tts_ms: null, playback_start_ms: null, total_ms: null, time_to_failure_ms: msBetween(turnStartAt, sttEndAt), failure_stage: 'stt', stt_failed: true, tts_failed: false });
      setState('ready');
      setVoiceNotice(stt.code === 'no_speech' ? 'No speech detected' : stt.code === 'not_configured' ? 'Mic unavailable' : stt.message);
      return;
    }
    setVersion((v) => v + 1);
    await runAssistantTurn(turnStartAt, micSubmitAt, 'voice', micCaptureMs, msBetween(sttStartAt, sttEndAt));
  };

  return (
    <main className="meeting-room session-shell" aria-label="askchappy session room">
      {/* ... unchanged render ... */}
      <header className="status-bar top-meeting-bar" aria-label="top meeting bar">
        <h1>AskChappy</h1>
        <p>{MODE_LOOKUP[activeMode].title} • {STATE_COPY[state]} • session {sessionId}{user?.role === 'admin' ? ' • Admin' : ''}</p>
      </header>
      <div className="meeting-content" aria-label="meeting body">
        <section className="meeting-stage" aria-label="meeting stage"><ChappyStage state={state} /></section>
        <section className="meeting-side-column" aria-label="meeting side column">
          <TranscriptPanel messages={messages} />
        </section>
      </div>
      {showModes ? <div className="modes-overlay card panel" role="dialog" aria-label="guided modes overlay"><button className="btn secondary" type="button" onClick={() => setShowModes(false)}>Close</button><SessionRightRail activeMode={session.metadata.askchappy.session_mode} generatedPresentation={session.metadata.askchappy.create_presentations_state?.generatedPresentation} onSelectMode={(mode) => { onSelectMode(mode); setShowModes(false); }} /></div> : null}
      <section className="meeting-toolbar" aria-label="bottom meeting toolbar">
        <div className="toolbar-notice" role="status">{voiceNotice}{runtimeNotice ? ` • ${runtimeNotice}` : ''}</div>
        <div className="toolbar-controls">
          <VoiceInput compact onStart={() => { micCaptureStartRef.current = performance.now(); setState('listening'); setVoiceNotice('Listening…'); pushDiagnostic('mic listening started'); }} onStop={() => { pushDiagnostic('mic capture submitted'); setState('transcribing'); setVoiceNotice('Transcribing…'); }} onTranscribe={onTranscribeVoice} onError={(message) => { setState('error'); setVoiceNotice(message); }} />
          <TypedInput compact onSubmitText={onSubmitText} />
          <button
            className={`meeting-control-btn output-control ${chappyMuted ? 'muted' : ''}`}
            type="button"
            onClick={() => {
              setChappyMuted((m) => !m);
              setVoiceNotice(chappyMuted ? 'Chappy voice on' : 'Chappy voice muted. Transcript only.');
              pushDiagnostic(chappyMuted ? 'Chappy unmuted' : 'Chappy muted');
            }}
          >
            <span className="meeting-control-icon" aria-hidden="true">{chappyMuted ? '🔇' : '🔊'}</span>
            <span className="meeting-control-label">{chappyMuted ? 'Unmute' : 'Mute Chappy'}</span>
          </button>
          <LocalRuntimeStatus compact />
          <button className="meeting-control-btn utility-control" type="button" onClick={() => setShowModes(true)}>
            <span className="meeting-control-icon" aria-hidden="true">▦</span>
            <span className="meeting-control-label">Modes</span>
          </button>
          {isCreatePresentationsMode ? (
            <button className="meeting-control-btn utility-control" type="button" onClick={() => onSelectMode('open_qa')}>
              <span className="meeting-control-icon" aria-hidden="true">↩</span>
              <span className="meeting-control-label">Exit Create Presentations</span>
            </button>
          ) : null}
          {user?.role === 'admin' ? (
            <button
              className="meeting-control-btn utility-control"
              type="button"
              onClick={() => {
                setShowAdminModal(true);
                pushDiagnostic('admin modal opened');
              }}
            >
              <span className="meeting-control-icon" aria-hidden="true">⚙</span>
              <span className="meeting-control-label">Admin</span>
            </button>
          ) : null}
        </div>
        <div className="toolbar-subnotice">
          {!voiceStatus.standard_tts_configured ? <span className="mini-badge">Chappy voice unavailable. Transcript response is still available.</span> : null}
        </div>
      </section>
      <AdminRuntimeConsoleModal isOpen={showAdminModal && user?.role === 'admin'} onClose={() => setShowAdminModal(false)} browserMicStatus={voiceNotice.includes('Permission denied') ? 'permission_denied' : 'available_or_unknown'} diagnostics={diagnostics} turnLatency={turnLatency} />
    </main>
  );
};
