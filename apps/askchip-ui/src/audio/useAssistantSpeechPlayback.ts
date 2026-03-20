import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TranscriptMessage } from '../types/contract';
import { askChipApiClient } from '../api/client';
import { findNextSpeechMessage } from './assistantSpeechHelpers';

export function useAssistantSpeechPlayback(sessionId: string | null, messages: TranscriptMessage[]) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const activeMessageIdRef = useRef<string | null>(null);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const nextMessage = useMemo(() => findNextSpeechMessage(messages), [messages]);

  const stop = useCallback(async (reason: string) => {
    const currentAudio = audioRef.current;
    const currentMessageId = activeMessageIdRef.current;
    if (!currentAudio || !sessionId || !currentMessageId) {
      return;
    }
    currentAudio.pause();
    currentAudio.currentTime = 0;
    audioRef.current = null;
    activeMessageIdRef.current = null;
    setActiveMessageId(null);
    await askChipApiClient.stopAssistantSpeech(sessionId, currentMessageId, reason);
  }, [sessionId]);

  const play = useCallback(async (message: TranscriptMessage) => {
    if (!sessionId) {
      return;
    }
    if (activeMessageIdRef.current === message.id) {
      return;
    }

    try {
      setSpeechError(null);
      const audio = await askChipApiClient.getAssistantSpeech(sessionId, message.id);
      audioRef.current = audio;
      activeMessageIdRef.current = message.id;
      setActiveMessageId(message.id);
      await askChipApiClient.startAssistantSpeech(sessionId, message.id);
      await audio.play();
      audio.addEventListener('ended', () => {
        void stop('ended');
      }, { once: true });
    } catch (error) {
      audioRef.current = null;
      activeMessageIdRef.current = null;
      setActiveMessageId(null);
      setSpeechError(error instanceof Error ? error.message : 'Assistant speech playback failed.');
    }
  }, [sessionId, stop]);

  useEffect(() => {
    if (!nextMessage || !sessionId || activeMessageIdRef.current) {
      return;
    }
    void play(nextMessage);
  }, [nextMessage, play, sessionId]);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!sessionId && audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
      activeMessageIdRef.current = null;
      setActiveMessageId(null);
    }
  }, [sessionId]);

  return {
    activeMessageId,
    speechError,
    stop,
  };
}
