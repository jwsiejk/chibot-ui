import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, askChipApiClient } from '../api/client';
import { askChipEventsClient, type ConnectionState } from '../api/events';
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
const MAX_RECENT_EVENTS = 24;
const MAX_RECENT_TIMINGS = 12;

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
  appError: string | null;
  wsNotice: string | null;
  sendingDisabledReason: string | null;
}

function sortSessions(items: SessionRecord[]): SessionRecord[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function dedupeEvents(events: EventRecord[]): EventRecord[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (seen.has(event.id)) {
      return false;
    }
    seen.add(event.id);
    return true;
  });
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
  const [appError, setAppError] = useState<string | null>(null);
  const [wsNotice, setWsNotice] = useState<string | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);

  const currentSession = useMemo(
    () => sessions.find((session) => session.id === currentSessionId) ?? null,
    [currentSessionId, sessions],
  );

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

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
    }
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      setConnectionState('connecting');
      setWsNotice('Attempting to reconnect to the backend event stream.');
    }, 1200);
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const handleEvent = useCallback((event: AskChipEvent) => {
    if (event.type === 'state') {
      const nextState = event.payload.state;
      if (nextState === 'ready' || nextState === 'thinking' || nextState === 'error') {
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

    if (event.type === 'turn.committed' || event.type === 'assistant.started') {
      if (activeSessionIdRef.current) {
        void loadTranscript(activeSessionIdRef.current);
      }
      return;
    }

    if (event.type === 'assistant.delta') {
      const messageId = typeof event.payload.message_id === 'string' ? event.payload.message_id : null;
      const delta = typeof event.payload.delta === 'string' ? event.payload.delta : '';
      if (!messageId || !delta) {
        return;
      }
      setMessages((existing) =>
        existing.map((message) =>
          message.id === messageId
            ? {
                ...message,
                status: 'streaming',
                text: `${message.text}${delta}`,
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === 'assistant.completed' || event.type === 'error') {
      if (activeSessionIdRef.current) {
        void loadTranscript(activeSessionIdRef.current);
      }
    }
  }, [loadTranscript]);

  useEffect(() => {
    if (currentSessionId === null) {
      setConnectionState('disconnected');
      return;
    }

    if (connectionState !== 'connecting') {
      return;
    }

    const disconnect = askChipEventsClient.connect({
      sessionId: currentSessionId,
      onOpen: () => {
        setConnectionState('connected');
        setWsNotice(null);
      },
      onClose: () => {
        setConnectionState('disconnected');
        setWsNotice('WebSocket disconnected. Streaming updates are unavailable until reconnection succeeds.');
        scheduleReconnect();
      },
      onError: () => {
        setConnectionState('disconnected');
        setWsNotice('WebSocket connection failed.');
      },
      onMessage: (event) => {
        setEvents((existing) => dedupeEvents([...existing, event]).slice(-MAX_RECENT_EVENTS));
        handleEvent(event);
      },
    });

    return () => disconnect();
  }, [connectionState, currentSessionId, handleEvent, scheduleReconnect]);

  useEffect(() => {
    setConnectionState(currentSessionId ? 'connecting' : 'disconnected');
  }, [currentSessionId]);

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

  const sendTurn = useCallback(async (text: string) => {
    if (!currentSessionId) {
      throw new Error('Create a session before sending a message.');
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
  }, [currentSessionId, loadSessions, loadTranscript]);

  const sendingDisabledReason = !currentSessionId
    ? 'Create or select a session to start a typed chat.'
    : pendingTurn
      ? 'Assistant is processing the current typed turn.'
      : null;

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
      appError,
      wsNotice,
      sendingDisabledReason,
    } satisfies AskChipControllerState,
    actions: {
      createSession,
      selectSession,
      reloadTranscript,
      sendTurn,
    },
  };
}
