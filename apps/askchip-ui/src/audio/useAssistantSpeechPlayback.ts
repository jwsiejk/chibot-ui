import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';
import { askChipApiClient } from '../api/client';
import {
  AssistantSpeechPlaybackCanceledError,
  createBackendSpeechStartHandshake,
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
    active.audio.pause();
    active.audio.currentTime = 0;
    URL.revokeObjectURL(active.objectUrl);
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

    try {
      setSpeechError(null);
      const { audio, objectUrl } = await askChipApiClient.getAssistantSpeech(sessionId, message.id);
      const active: ActivePlayback = {
        audio,
        messageId: message.id,
        objectUrl,
        sessionId,
        waitController: new AbortController(),
        backendHandshake: createBackendSpeechStartHandshake((reason) => askChipApiClient.stopAssistantSpeech(sessionId, message.id, reason)),
      };
      activePlaybackRef.current = active;
      setActiveMessageId(message.id);
      audio.addEventListener('ended', () => {
        void finalizePlayback('ended');
      }, { once: true });
      audio.addEventListener('error', () => {
        void finalizePlayback('playback_error');
      }, { once: true });

      await waitForPlaybackStart(audio, {
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
        active.backendHandshake.failStart();
        throw error;
      }
      await active.backendHandshake.acknowledgeStart();
      if (activePlaybackRef.current !== active) {
        return;
      }
    } catch (error) {
      if (error instanceof AssistantSpeechPlaybackCanceledError) {
        return;
      }
      const isCurrent = activePlaybackRef.current?.messageId === message.id;
      if (isCurrent) {
        const objectUrl = activePlaybackRef.current?.objectUrl;
        activePlaybackRef.current?.audio.pause();
        activePlaybackRef.current = null;
        setActiveMessageId(null);
        if (objectUrl) {
          URL.revokeObjectURL(objectUrl);
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
