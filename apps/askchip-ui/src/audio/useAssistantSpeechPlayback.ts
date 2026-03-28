import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';
import { ApiError, askChipApiClient } from '../api/client';
import {
  AssistantSpeechPlaybackCanceledError,
  cleanupFetchedAssistantSpeech,
  createBackendSpeechStartHandshake,
  createPlaybackAttemptTracker,
  findNextSpeechChunk,
  waitForPlaybackStart,
  type SpeechChunkCandidate,
} from './assistantSpeechHelpers';

type ActivePlayback = {
  audio: HTMLAudioElement;
  messageId: string;
  objectUrl: string;
  sessionId: string;
  waitController: AbortController;
  backendHandshake: ReturnType<typeof createBackendSpeechStartHandshake>;
  spokenThrough: number;
};

export function useAssistantSpeechPlayback(sessionId: string | null, messages: TranscriptMessage[], options?: { onMetric?: (metric: { traceId: string | null; name: string; at: number; info?: Record<string, unknown> }) => void }) {
  const activePlaybackRef = useRef<ActivePlayback | null>(null);
  const playbackAttemptTrackerRef = useRef(createPlaybackAttemptTracker());
  const latestSessionIdRef = useRef<string | null>(sessionId);
  const previousMessagesRef = useRef<TranscriptMessage[]>([]);
  const previousSessionIdRef = useRef<string | null>(sessionId);
  const cleanupSessionIdRef = useRef<string | null>(sessionId);
  const spokenOffsetsRef = useRef(new Map<string, number>());
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [pendingMessageId, setPendingMessageId] = useState<string | null>(null);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [playbackProgressVersion, setPlaybackProgressVersion] = useState(0);

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
    spokenOffsetsRef.current.set(active.messageId, active.spokenThrough);
    setPlaybackProgressVersion((version) => version + 1);
    setActiveMessageId(null);

    try {
      await active.backendHandshake.cancel(reason);
    } catch (error) {
      setSpeechError(error instanceof Error ? error.message : 'Assistant speech playback cleanup failed.');
    }
  }, []);

  const stop = useCallback(async (reason: string) => {
    const invalidatedAttempt = playbackAttemptTrackerRef.current.invalidate();
    if (invalidatedAttempt) {
      setPendingMessageId(null);
    }
    await finalizePlayback(reason);
  }, [finalizePlayback]);

  const nextChunk = useMemo(() => findNextSpeechChunk({
    messages,
    previousMessages: previousMessagesRef.current,
    spokenOffsets: spokenOffsetsRef.current,
    sessionChanged: previousSessionIdRef.current !== sessionId,
  }), [messages, sessionId, playbackProgressVersion]);

  useEffect(() => {
    previousMessagesRef.current = messages;
    previousSessionIdRef.current = sessionId;
  }, [messages, sessionId]);

  const play = useCallback(async (candidate: SpeechChunkCandidate) => {
    if (!sessionId || activePlaybackRef.current) {
      return;
    }

    const currentAttempt = playbackAttemptTrackerRef.current.current();
    if (currentAttempt && currentAttempt.sessionId === sessionId) {
      return;
    }

    const attempt = playbackAttemptTrackerRef.current.reserve(sessionId, candidate.message.id);

    try {
      setSpeechError(null);
      setPendingMessageId(candidate.message.id);
      const traceId = typeof candidate.message.metadata?.trace_id === 'string' ? candidate.message.metadata.trace_id : null;
      const fetched = await askChipApiClient.getAssistantSpeech(sessionId, candidate.message.id, candidate.chunkText, traceId ?? undefined);
      options?.onMetric?.({ traceId, name: 'audio_fetch_start', at: fetched.fetchStartedAt });
      options?.onMetric?.({ traceId, name: 'audio_fetch_end', at: fetched.fetchEndedAt, info: { chunk_chars: candidate.chunkText.length } });
      const isStaleAttempt = !playbackAttemptTrackerRef.current.isCurrent(attempt) || latestSessionIdRef.current !== sessionId;
      if (isStaleAttempt) {
        cleanupFetchedAssistantSpeech(fetched);
        playbackAttemptTrackerRef.current.clear(attempt);
        setPendingMessageId(null);
        return;
      }

      const active: ActivePlayback = {
        ...fetched,
        messageId: candidate.message.id,
        sessionId,
        spokenThrough: candidate.spokenThrough,
        waitController: new AbortController(),
        backendHandshake: createBackendSpeechStartHandshake((reason) => askChipApiClient.stopAssistantSpeech(sessionId, candidate.message.id, reason)),
      };
      activePlaybackRef.current = active;
      setActiveMessageId(candidate.message.id);
      active.audio.addEventListener('ended', () => {
        options?.onMetric?.({ traceId, name: 'audio_playback_end', at: Date.now() });
        void finalizePlayback('ended');
      }, { once: true });
      active.audio.addEventListener('error', () => {
        void finalizePlayback('playback_error');
      }, { once: true });

      await waitForPlaybackStart(active.audio, {
        signal: active.waitController.signal,
        cancellationError: new AssistantSpeechPlaybackCanceledError(),
      });
      options?.onMetric?.({ traceId, name: 'audio_playback_start', at: Date.now() });
      if (activePlaybackRef.current !== active || latestSessionIdRef.current !== sessionId) {
        await finalizePlayback('session_switch');
        return;
      }

      active.backendHandshake.beginStart();
      try {
        await askChipApiClient.startAssistantSpeech(sessionId, candidate.message.id);
      } catch (error) {
        try {
          await active.backendHandshake.failStart('start_failed');
        } catch {
          // preserve the original start failure as the surfaced error
        }
        throw error;
      }
      if (!playbackAttemptTrackerRef.current.isCurrent(attempt)) {
        await active.backendHandshake.cancel('stale_start');
      }
      await active.backendHandshake.acknowledgeStart();
      playbackAttemptTrackerRef.current.clear(attempt);
      setPendingMessageId(null);
      if (activePlaybackRef.current !== active) {
        return;
      }
    } catch (error) {
      playbackAttemptTrackerRef.current.clear(attempt);
      setPendingMessageId((current) => (current === candidate.message.id ? null : current));
      if (error instanceof AssistantSpeechPlaybackCanceledError) {
        return;
      }
      const isCurrent = activePlaybackRef.current?.messageId === candidate.message.id;
      if (isCurrent) {
        const active = activePlaybackRef.current;
        activePlaybackRef.current = null;
        setActiveMessageId(null);
        if (active) {
          cleanupFetchedAssistantSpeech(active);
        }
      }
      const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : 'Assistant speech playback failed.';
      setSpeechError(detail);
    }
  }, [finalizePlayback, options, sessionId]);

  useEffect(() => {
    if (!nextChunk || !sessionId || activePlaybackRef.current) {
      return;
    }
    void play(nextChunk);
  }, [nextChunk, play, sessionId]);

  useEffect(() => () => {
    void stop('unmount');
  }, [stop]);

  useEffect(() => {
    if (cleanupSessionIdRef.current !== sessionId) {
      cleanupSessionIdRef.current = sessionId;
      spokenOffsetsRef.current = new Map();
      void stop('session_switch');
    }
  }, [sessionId, stop]);

  return {
    activeMessageId,
    pendingMessageId,
    speechError,
    stop,
  };
}
