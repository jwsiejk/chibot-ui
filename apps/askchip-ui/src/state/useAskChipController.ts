import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, askChipApiClient } from '../api/client';
import { askChipEventsClient, type ConnectionState } from '../api/events';
import {
  applyAssistantStreamEvent,
  buildListeningDraft,
  buildTranscribingDraft,
  dedupeEvents,
  getRecoveredVoiceTopLevelState,
  getSendingDisabledReason,
  getVoiceDisabledReason,
  isTurnState,
  isVoiceLifecycleState,
  MAX_RECENT_EVENTS,
  MAX_RECENT_TIMINGS,
  type VoiceDraftState,
} from './controllerHelpers';
import type {
  AskChipEvent,
  ConfigResponse,
  EventRecord,
  SessionRecord,
  TimingRecord,
  TranscriptMessage,
  TurnState,
} from '../types/contract';

const STORAGE_KEY = 'askchip-ui.current-session-id';

export interface AskChipControllerState {
  sessions: SessionRecord[];
  currentSessionId: string | null;
  currentSession: SessionRecord | null;
  messages: TranscriptMessage[];
  events: EventRecord[];
  timings: TimingRecord[];
  config: ConfigResponse | null;
  connectionState: ConnectionState;
  topLevelState: TurnState | null;
  pendingTurn: boolean;
  voiceDraft: VoiceDraftState | null;
  appError: string | null;
  wsNotice: string | null;
  sendingDisabledReason: string | null;
  voiceDisabledReason: string | null;
}

function sortSessions(items: SessionRecord[]): SessionRecord[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function useAskChipController() {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [timings, setTimings] = useState<TimingRecord[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting');
  const [topLevelState, setTopLevelState] = useState<TurnState | null>(null);
  const [pendingTurn, setPendingTurn] = useState(false);
  const [voiceDraft, setVoiceDraft] = useState<VoiceDraftState | null>(null);
  const [appError, setAppError] = useState<string | null>(null);
  const [wsNotice, setWsNotice] = useState<string | null>(null);
  const [reconnectKey, setReconnectKey] = useState(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);

  const currentSession = useMemo(
    () => sessions.find((session) => session.id === currentSessionId) ?? null,
    [currentSessionId, sessions],
  );

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const loadSessions = useCallback(async () => {
    const nextSessions = sortSessions(await askChipApiClient.listSessions());
    setSessions(nextSessions);
    return nextSessions;
  }, []);

  const loadTranscript = useCallback(async (sessionId: string) => {
    const transcript = await askChipApiClient.getTranscript(sessionId);
    setMessages(transcript.messages);
    setEvents(dedupeEvents(transcript.events).slice(-MAX_RECENT_EVENTS));
    setTimings(transcript.timings.slice(-MAX_RECENT_TIMINGS));
    setTopLevelState(transcript.session.status);
    setSessions((existing) => sortSessions(existing.map((session) => (session.id === transcript.session.id ? transcript.session : session))));
    return transcript;
  }, []);

  const selectSession = useCallback(async (sessionId: string) => {
    activeSessionIdRef.current = sessionId;
    setCurrentSessionId(sessionId);
    setVoiceDraft(null);
    localStorage.setItem(STORAGE_KEY, sessionId);
    setAppError(null);
    await loadTranscript(sessionId);
  }, [loadTranscript]);

  const bootstrap = useCallback(async () => {
    try {
      setAppError(null);
      const [nextConfig, nextSessions] = await Promise.all([askChipApiClient.getConfig(), loadSessions()]);
      setConfig(nextConfig);
      const storedId = localStorage.getItem(STORAGE_KEY);
      const preferredId = storedId && nextSessions.some((session) => session.id === storedId)
        ? storedId
        : nextSessions[0]?.id ?? null;

      if (preferredId) {
        await selectSession(preferredId);
      } else {
        setCurrentSessionId(null);
        setMessages([]);
        setEvents([]);
        setTimings([]);
        setTopLevelState(null);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to reach AskChip API.';
      setAppError(message);
    }
  }, [loadSessions, selectSession]);

  const scheduleReconnect = useCallback((notice: string) => {
    clearReconnectTimer();
    setConnectionState('disconnected');
    setWsNotice(notice);
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      setConnectionState('connecting');
      setWsNotice('Attempting to reconnect to the backend event stream.');
      setReconnectKey((current) => current + 1);
    }, 1200);
  }, [clearReconnectTimer]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const handleEvent = useCallback((event: AskChipEvent) => {
    if (event.type === 'state') {
      const nextState = event.payload.state;
      if (isTurnState(nextState)) {
        setTopLevelState(nextState);
        setSessions((existing) =>
          sortSessions(
            existing.map((session) =>
              session.id === activeSessionIdRef.current ? { ...session, status: nextState } : session,
            ),
          ),
        );
      }
      return;
    }

    if (event.type === 'assistant.started' || event.type === 'assistant.delta') {
      setMessages((existing) => applyAssistantStreamEvent(existing, event));
      if (event.type === 'assistant.started') {
        return;
      }
    }

    if (event.type === 'turn.committed') {
      setVoiceDraft(null);
      if (activeSessionIdRef.current) {
        void loadTranscript(activeSessionIdRef.current);
      }
      return;
    }

    if (event.type === 'assistant.completed' || event.type === 'error') {
      if (event.type === 'error') {
        setVoiceDraft(null);
      }
      if (activeSessionIdRef.current) {
        void loadTranscript(activeSessionIdRef.current);
      }
    }
  }, [loadTranscript]);

  useEffect(() => {
    if (currentSessionId === null) {
      clearReconnectTimer();
      setConnectionState('disconnected');
      setWsNotice(null);
      return;
    }

    setConnectionState('connecting');
    const disconnect = askChipEventsClient.connect({
      sessionId: currentSessionId,
      onOpen: () => {
        clearReconnectTimer();
        setConnectionState('connected');
        setWsNotice(null);
      },
      onClose: () => {
        scheduleReconnect('WebSocket disconnected. Streaming updates are unavailable until reconnection succeeds.');
      },
      onError: () => {
        scheduleReconnect('WebSocket connection failed. Retrying shortly.');
      },
      onMessage: (event) => {
        setEvents((existing) => dedupeEvents([...existing, event]).slice(-MAX_RECENT_EVENTS));
        handleEvent(event);
      },
    });

    return () => {
      clearReconnectTimer();
      disconnect();
    };
  }, [clearReconnectTimer, currentSessionId, handleEvent, reconnectKey, scheduleReconnect]);

  const createSession = useCallback(async () => {
    const session = await askChipApiClient.createSession({ title: 'New chat' });
    setSessions((existing) => sortSessions([session, ...existing]));
    await selectSession(session.id);
  }, [selectSession]);

  const reloadTranscript = useCallback(async () => {
    if (!currentSessionId) {
      return;
    }
    setAppError(null);
    try {
      await loadTranscript(currentSessionId);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to reload transcript.';
      setAppError(message);
    }
  }, [currentSessionId, loadTranscript]);

  const restoreVoiceStateFromBackend = useCallback(async (sessionId: string) => {
    try {
      await loadTranscript(sessionId);
    } catch (reloadError) {
      setTopLevelState((current) => getRecoveredVoiceTopLevelState(current));
      throw reloadError;
    }
  }, [loadTranscript]);

  const sendTurn = useCallback(async (text: string) => {
    if (!currentSessionId) {
      throw new Error('Create a session before sending a message.');
    }
    if (isVoiceLifecycleState(topLevelState)) {
      throw new Error('Release the active push-to-talk capture before sending a typed turn.');
    }
    if (topLevelState === 'thinking') {
      throw new Error('Assistant is already processing the current turn.');
    }

    setPendingTurn(true);
    setAppError(null);
    try {
      await askChipApiClient.createTurn(currentSessionId, { text });
      await Promise.all([loadSessions(), loadTranscript(currentSessionId)]);
    } catch (error) {
      if (error instanceof ApiError) {
        setAppError(error.detail);
      } else {
        setAppError(error instanceof Error ? error.message : 'Failed to send message.');
      }
      throw error;
    } finally {
      setPendingTurn(false);
    }
  }, [currentSessionId, loadSessions, loadTranscript, topLevelState]);

  const startVoiceTurn = useCallback(async (deviceId: string | null, startedAt: number | null) => {
    if (!currentSessionId) {
      throw new Error('Create a session before starting push-to-talk.');
    }
    if (pendingTurn || topLevelState === 'thinking') {
      throw new Error('Assistant is already processing the current turn.');
    }
    if (isVoiceLifecycleState(topLevelState)) {
      throw new Error('Push-to-talk capture is already active.');
    }
    setAppError(null);
    setVoiceDraft(buildListeningDraft(startedAt));
    setTopLevelState('listening');
    try {
      await askChipApiClient.startVoiceTurn(currentSessionId, deviceId);
    } catch (error) {
      setVoiceDraft(null);
      const message = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : 'Failed to start push-to-talk.';
      try {
        await restoreVoiceStateFromBackend(currentSessionId);
      } catch {
        // keep the original start failure visible if transcript reload also fails
      }
      setAppError(message);
      throw error;
    }
  }, [currentSessionId, pendingTurn, restoreVoiceStateFromBackend, topLevelState]);

  const finishVoiceTurn = useCallback(async (payload: { blob: Blob; filename: string; deviceId: string | null; durationMs: number; }) => {
    if (!currentSessionId) {
      throw new Error('Create a session before sending a voice turn.');
    }
    if (pendingTurn || topLevelState === 'thinking') {
      throw new Error('Assistant is already processing the current turn.');
    }
    setPendingTurn(true);
    setAppError(null);
    setVoiceDraft(buildTranscribingDraft(payload.durationMs));
    setTopLevelState('transcribing');
    try {
      await askChipApiClient.createVoiceTurn(currentSessionId, payload);
      await Promise.all([loadSessions(), loadTranscript(currentSessionId)]);
      setVoiceDraft(null);
    } catch (error) {
      setVoiceDraft(null);
      try {
        await Promise.all([loadSessions(), restoreVoiceStateFromBackend(currentSessionId)]);
      } catch {
        // keep the original release failure visible if recovery reload also fails
      }
      if (error instanceof ApiError) {
        setAppError(error.detail);
      } else {
        setAppError(error instanceof Error ? error.message : 'Failed to send voice turn.');
      }
      throw error;
    } finally {
      setPendingTurn(false);
    }
  }, [currentSessionId, loadSessions, loadTranscript, pendingTurn, restoreVoiceStateFromBackend, topLevelState]);

  const sendingDisabledReason = getSendingDisabledReason({ currentSessionId, pendingTurn, topLevelState });

  const voiceDisabledReason = getVoiceDisabledReason({ currentSessionId, pendingTurn, topLevelState });

  return {
    state: {
      sessions,
      currentSessionId,
      currentSession,
      messages,
      events,
      timings,
      config,
      connectionState,
      topLevelState,
      pendingTurn,
      voiceDraft,
      appError,
      wsNotice,
      sendingDisabledReason,
      voiceDisabledReason,
    } satisfies AskChipControllerState,
    actions: {
      createSession,
      selectSession,
      reloadTranscript,
      sendTurn,
      startVoiceTurn,
      finishVoiceTurn,
      cancelVoiceTurn: async () => {
        if (!currentSessionId) {
          return;
        }
        setVoiceDraft(null);
        setAppError(null);
        try {
          await askChipApiClient.cancelVoiceTurn(currentSessionId);
        } finally {
          try {
            await Promise.all([loadSessions(), restoreVoiceStateFromBackend(currentSessionId)]);
          } catch {
            setTopLevelState((current) => getRecoveredVoiceTopLevelState(current));
          }
        }
      },
    },
  };
}
