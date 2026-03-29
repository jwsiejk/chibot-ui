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
  type PlaybackAttemptReservation,
  type SpeechChunkCandidate,
} from './assistantSpeechHelpers';

type PreparedSpeechClip = {
  audio: HTMLAudioElement;
  objectUrl: string;
  fetchStartedAt: number;
  fetchEndedAt: number;
  messageId: string;
  sessionId: string;
  spokenThrough: number;
  chunkKey: string;
  traceId: string | null;
};

type ActivePlayback = PreparedSpeechClip & {
  waitController: AbortController;
  backendHandshake: ReturnType<typeof createBackendSpeechStartHandshake>;
};

type PendingFetch = {
  attempt: PlaybackAttemptReservation;
  chunkKey: string;
  messageId: string;
  sessionId: string;
};

function speechChunkKey(candidate: SpeechChunkCandidate): string {
  return `${candidate.message.id}:${candidate.spokenThrough}`;
}

export function useAssistantSpeechPlayback(sessionId: string | null, messages: TranscriptMessage[], options?: { onMetric?: (metric: { traceId: string | null; name: string; at: number; info?: Record<string, unknown> }) => void }) {
  const activePlaybackRef = useRef<ActivePlayback | null>(null);
  const prefetchedPlaybackRef = useRef<PreparedSpeechClip | null>(null);
  const pendingFetchRef = useRef<PendingFetch | null>(null);
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

  const cleanupPrefetchedClip = useCallback(() => {
    const prefetched = prefetchedPlaybackRef.current;
    if (!prefetched) {
      return;
    }
    prefetchedPlaybackRef.current = null;
    cleanupFetchedAssistantSpeech(prefetched);
  }, []);

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
    pendingFetchRef.current = null;
    cleanupPrefetchedClip();
    await finalizePlayback(reason);
  }, [cleanupPrefetchedClip, finalizePlayback]);

  const nextChunk = useMemo(() => {
    const effectiveOffsets = new Map(spokenOffsetsRef.current);
    if (activePlaybackRef.current) {
      const active = activePlaybackRef.current;
      const existing = effectiveOffsets.get(active.messageId) ?? 0;
      effectiveOffsets.set(active.messageId, Math.max(existing, active.spokenThrough));
    }
    if (prefetchedPlaybackRef.current) {
      const prefetched = prefetchedPlaybackRef.current;
      const existing = effectiveOffsets.get(prefetched.messageId) ?? 0;
      effectiveOffsets.set(prefetched.messageId, Math.max(existing, prefetched.spokenThrough));
    }
    if (pendingFetchRef.current) {
      const pending = pendingFetchRef.current;
      const candidateThrough = Number.parseInt(pending.chunkKey.split(':')[1] ?? '0', 10);
      if (!Number.isNaN(candidateThrough)) {
        const existing = effectiveOffsets.get(pending.messageId) ?? 0;
        effectiveOffsets.set(pending.messageId, Math.max(existing, candidateThrough));
      }
    }

    return findNextSpeechChunk({
      messages,
      previousMessages: previousMessagesRef.current,
      spokenOffsets: effectiveOffsets,
      sessionChanged: previousSessionIdRef.current !== sessionId,
    });
  }, [messages, sessionId, playbackProgressVersion]);

  useEffect(() => {
    previousMessagesRef.current = messages;
    previousSessionIdRef.current = sessionId;
  }, [messages, sessionId]);

  const activatePrefetchedClip = useCallback(async (clip: PreparedSpeechClip, source: 'prefetched' | 'direct') => {
    if (!sessionId || clip.sessionId !== sessionId || latestSessionIdRef.current !== sessionId) {
      cleanupFetchedAssistantSpeech(clip);
      return;
    }

    const active: ActivePlayback = {
      ...clip,
      waitController: new AbortController(),
      backendHandshake: createBackendSpeechStartHandshake((reason) => askChipApiClient.stopAssistantSpeech(sessionId, clip.messageId, reason)),
    };
    activePlaybackRef.current = active;
    setActiveMessageId(clip.messageId);
    setPendingMessageId((current) => (current === clip.messageId ? null : current));

    const onEnded = () => {
      options?.onMetric?.({ traceId: clip.traceId, name: 'audio_playback_end', at: Date.now(), info: { source } });
      void finalizePlayback('ended');
    };
    active.audio.addEventListener('ended', onEnded, { once: true });
    active.audio.addEventListener('error', () => {
      void finalizePlayback('playback_error');
    }, { once: true });

    await waitForPlaybackStart(active.audio, {
      signal: active.waitController.signal,
      cancellationError: new AssistantSpeechPlaybackCanceledError(),
    });
    options?.onMetric?.({ traceId: clip.traceId, name: 'audio_playback_start', at: Date.now(), info: { source } });

    if (activePlaybackRef.current !== active || latestSessionIdRef.current !== sessionId) {
      await finalizePlayback('session_switch');
      return;
    }

    active.backendHandshake.beginStart();
    try {
      await askChipApiClient.startAssistantSpeech(sessionId, clip.messageId);
    } catch (error) {
      try {
        await active.backendHandshake.failStart('start_failed');
      } catch {
        // preserve original start failure
      }
      throw error;
    }
    await active.backendHandshake.acknowledgeStart();
  }, [finalizePlayback, options, sessionId]);

  const prefetchChunk = useCallback(async (candidate: SpeechChunkCandidate) => {
    if (!sessionId || latestSessionIdRef.current !== sessionId) {
      return;
    }

    const chunkKey = speechChunkKey(candidate);
    if (activePlaybackRef.current?.chunkKey === chunkKey || prefetchedPlaybackRef.current?.chunkKey === chunkKey || pendingFetchRef.current?.chunkKey === chunkKey) {
      return;
    }

    const attempt = playbackAttemptTrackerRef.current.reserve(sessionId, candidate.message.id);
    pendingFetchRef.current = {
      attempt,
      chunkKey,
      messageId: candidate.message.id,
      sessionId,
    };

    try {
      setSpeechError(null);
      setPendingMessageId(candidate.message.id);
      const traceId = typeof candidate.message.metadata?.trace_id === 'string' ? candidate.message.metadata.trace_id : null;
      const fetched = await askChipApiClient.getAssistantSpeech(sessionId, candidate.message.id, candidate.chunkText, traceId ?? undefined);
      options?.onMetric?.({ traceId, name: 'audio_fetch_start', at: fetched.fetchStartedAt, info: { chunk_key: chunkKey } });
      options?.onMetric?.({ traceId, name: 'audio_fetch_end', at: fetched.fetchEndedAt, info: { chunk_key: chunkKey, chunk_chars: candidate.chunkText.length } });

      const stale = !playbackAttemptTrackerRef.current.isCurrent(attempt)
        || latestSessionIdRef.current !== sessionId
        || pendingFetchRef.current?.chunkKey !== chunkKey;
      if (stale) {
        cleanupFetchedAssistantSpeech(fetched);
        playbackAttemptTrackerRef.current.clear(attempt);
        if (pendingFetchRef.current?.chunkKey === chunkKey) {
          pendingFetchRef.current = null;
        }
        setPendingMessageId(null);
        return;
      }

      const clip: PreparedSpeechClip = {
        ...fetched,
        messageId: candidate.message.id,
        sessionId,
        spokenThrough: candidate.spokenThrough,
        chunkKey,
        traceId,
      };

      pendingFetchRef.current = null;
      playbackAttemptTrackerRef.current.clear(attempt);
      setPendingMessageId(null);

      if (!activePlaybackRef.current) {
        await activatePrefetchedClip(clip, 'direct');
        return;
      }

      cleanupPrefetchedClip();
      prefetchedPlaybackRef.current = clip;
      setPlaybackProgressVersion((version) => version + 1);
    } catch (error) {
      playbackAttemptTrackerRef.current.clear(attempt);
      if (pendingFetchRef.current?.chunkKey === chunkKey) {
        pendingFetchRef.current = null;
      }
      setPendingMessageId((current) => (current === candidate.message.id ? null : current));
      if (error instanceof AssistantSpeechPlaybackCanceledError) {
        return;
      }
      const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : 'Assistant speech playback failed.';
      setSpeechError(detail);
    }
  }, [activatePrefetchedClip, cleanupPrefetchedClip, options, sessionId]);

  useEffect(() => {
    if (!nextChunk || !sessionId) {
      return;
    }
    void prefetchChunk(nextChunk);
  }, [nextChunk, prefetchChunk, sessionId]);

  useEffect(() => {
    const active = activePlaybackRef.current;
    if (!active) {
      const prefetched = prefetchedPlaybackRef.current;
      if (prefetched) {
        prefetchedPlaybackRef.current = null;
        void activatePrefetchedClip(prefetched, 'prefetched');
      }
    }
  }, [activatePrefetchedClip, playbackProgressVersion]);

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
