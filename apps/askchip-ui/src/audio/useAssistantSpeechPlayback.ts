import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';
import { askChipApiClient } from '../api/client';
import {
  AssistantSpeechPlaybackCanceledError,
  cleanupFetchedAssistantSpeech,
  createBackendSpeechStartHandshake,
  createPlaybackAttemptTracker,
  findNextSpeechMessage,
  waitForPlaybackStart,
} from './assistantSpeechHelpers';


type ActivePlayback = {
  audio: HTMLAudioElement;
  messageId: string;
  objectUrl: string;
  sessionId: string;
  waitController: AbortController;
  backendHandshake: ReturnType<typeof createBackendSpeechStartHandshake>;
};

export function useAssistantSpeechPlayback(sessionId: string | null, messages: TranscriptMessage[]) {
  const activePlaybackRef = useRef<ActivePlayback | null>(null);
  const playbackAttemptTrackerRef = useRef(createPlaybackAttemptTracker());
  const latestSessionIdRef = useRef<string | null>(sessionId);
  const previousMessagesRef = useRef<TranscriptMessage[]>([]);
  const previousSessionIdRef = useRef<string | null>(sessionId);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [speechError, setSpeechError] = useState<string | null>(null);

  useEffect(() => {
    latestSessionIdRef.current = sessionId;
  }, [sessionId]);

  const finalizePlayback = useCallback(async (reason: string) => {
    const active = activePlaybackRef.current;
    if (!active) {
      return;
    }

    activePlaybackRef.current = null;
    active.waitController.abort();
    cleanupFetchedAssistantSpeech(active);
    setActiveMessageId(null);

    try {
      await active.backendHandshake.cancel(reason);
    } catch (error) {
      setSpeechError(error instanceof Error ? error.message : 'Assistant speech playback cleanup failed.');
    }
  }, []);

  const stop = useCallback(async (reason: string) => {
    await finalizePlayback(reason);
  }, [finalizePlayback]);

  const nextMessage = useMemo(() => findNextSpeechMessage({
    messages,
    previousMessages: previousMessagesRef.current,
    sessionChanged: previousSessionIdRef.current !== sessionId,
  }), [messages, sessionId]);

  useEffect(() => {
    previousMessagesRef.current = messages;
    previousSessionIdRef.current = sessionId;
  }, [messages, sessionId]);

  const play = useCallback(async (message: TranscriptMessage) => {
    if (!sessionId || activePlaybackRef.current) {
      return;
    }

    const currentAttempt = playbackAttemptTrackerRef.current.current();
    if (currentAttempt && currentAttempt.sessionId === sessionId) {
      return;
    }

    const attempt = playbackAttemptTrackerRef.current.reserve(sessionId, message.id);

    try {
      setSpeechError(null);
      const fetched = await askChipApiClient.getAssistantSpeech(sessionId, message.id);
      const isStaleAttempt = !playbackAttemptTrackerRef.current.isCurrent(attempt) || latestSessionIdRef.current !== sessionId;
      if (isStaleAttempt) {
        cleanupFetchedAssistantSpeech(fetched);
        playbackAttemptTrackerRef.current.clear(attempt);
        return;
      }

      const active: ActivePlayback = {
        ...fetched,
        messageId: message.id,
        sessionId,
        waitController: new AbortController(),
        backendHandshake: createBackendSpeechStartHandshake((reason) => askChipApiClient.stopAssistantSpeech(sessionId, message.id, reason)),
      };
      activePlaybackRef.current = active;
      playbackAttemptTrackerRef.current.clear(attempt);
      setActiveMessageId(message.id);
      active.audio.addEventListener('ended', () => {
        void finalizePlayback('ended');
      }, { once: true });
      active.audio.addEventListener('error', () => {
        void finalizePlayback('playback_error');
      }, { once: true });

      await waitForPlaybackStart(active.audio, {
        signal: active.waitController.signal,
        cancellationError: new AssistantSpeechPlaybackCanceledError(),
      });
      if (activePlaybackRef.current !== active || latestSessionIdRef.current !== sessionId) {
        await finalizePlayback('session_switch');
        return;
      }

      active.backendHandshake.beginStart();
      try {
        await askChipApiClient.startAssistantSpeech(sessionId, message.id);
      } catch (error) {
        try {
          await active.backendHandshake.failStart('start_failed');
        } catch {
          // preserve the original start failure as the surfaced error
        }
        throw error;
      }
      await active.backendHandshake.acknowledgeStart();
      if (activePlaybackRef.current !== active) {
        return;
      }
    } catch (error) {
      playbackAttemptTrackerRef.current.clear(attempt);
      if (error instanceof AssistantSpeechPlaybackCanceledError) {
        return;
      }
      const isCurrent = activePlaybackRef.current?.messageId === message.id;
      if (isCurrent) {
        const active = activePlaybackRef.current;
        activePlaybackRef.current = null;
        setActiveMessageId(null);
        if (active) {
          cleanupFetchedAssistantSpeech(active);
        }
      }
      setSpeechError(error instanceof Error ? error.message : 'Assistant speech playback failed.');
    }
  }, [finalizePlayback, sessionId]);

  useEffect(() => {
    if (!nextMessage || !sessionId || activePlaybackRef.current) {
      return;
    }
    void play(nextMessage);
  }, [nextMessage, play, sessionId]);

  useEffect(() => () => {
    void finalizePlayback('unmount');
  }, [finalizePlayback]);

  useEffect(() => {
    const active = activePlaybackRef.current;
    if (sessionId !== latestSessionIdRef.current) {
      return;
    }
    if (!active) {
      return;
    }
    if (sessionId !== active.sessionId) {
      void finalizePlayback('session_switch');
    }
  }, [finalizePlayback, sessionId]);

  return {
    activeMessageId,
    speechError,
    stop,
  };
}
