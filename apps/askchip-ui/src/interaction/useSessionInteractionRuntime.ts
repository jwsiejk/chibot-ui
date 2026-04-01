import { useCallback, useEffect, useMemo, useRef } from 'react';
import { createPttLifecycleController } from '../audio/pttLifecycle';
import { useAssistantSpeechPlayback } from '../audio/useAssistantSpeechPlayback';
import { useAudioFoundation } from '../audio/useAudioFoundation';
import { usePushToTalkRecorder } from '../audio/usePushToTalkRecorder';
import { useAskChipController } from '../state/useAskChipController';

function isVoiceLifecycleState(state: string | null): state is 'listening' | 'transcribing' {
  return state === 'listening' || state === 'transcribing';
}

interface SessionInteractionRuntimeOptions {
  initialSessionId?: string | null;
}

export function useSessionInteractionRuntime(options: SessionInteractionRuntimeOptions = {}) {
  const { state, actions } = useAskChipController({ initialSessionId: options.initialSessionId });
  const audio = useAudioFoundation(state.currentSessionId);
  const pushToTalk = usePushToTalkRecorder(audio.selectedDeviceId);
  const speech = useAssistantSpeechPlayback(state.currentSessionId, state.messages);

  const interruptAssistantSpeech = useCallback(async (reason: string) => {
    if (state.topLevelState === 'speaking' || speech.pendingMessageId || speech.activeMessageId) {
      await speech.stop(reason);
    }
  }, [speech, state.topLevelState]);

  const sendTypedTurn = useCallback(async (text: string) => {
    await interruptAssistantSpeech('typed_submit');
    await actions.sendTurn(text);
  }, [actions, interruptAssistantSpeech]);

  const pttRuntimeRef = useRef({
    actions,
    audioSelectedDeviceId: audio.selectedDeviceId,
    pushToTalkActions: pushToTalk.actions,
    pushToTalkActive: pushToTalk.active,
    voiceDisabledReason: state.voiceDisabledReason,
    interruptAssistantSpeech,
  });

  useEffect(() => {
    pttRuntimeRef.current = {
      actions,
      audioSelectedDeviceId: audio.selectedDeviceId,
      pushToTalkActions: pushToTalk.actions,
      pushToTalkActive: pushToTalk.active,
      voiceDisabledReason: state.voiceDisabledReason,
      interruptAssistantSpeech,
    };
  }, [actions, audio.selectedDeviceId, interruptAssistantSpeech, pushToTalk.actions, pushToTalk.active, state.voiceDisabledReason]);

  const pttLifecycle = useMemo(() => createPttLifecycleController({
    beginLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.beginCapture(),
    finishLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.finishCapture(),
    cancelLocalCapture: () => pttRuntimeRef.current.pushToTalkActions.cancelCapture(),
    startBackendVoiceTurn: async () => {
      await pttRuntimeRef.current.interruptAssistantSpeech('ptt_start');
      return pttRuntimeRef.current.actions.startVoiceTurn(
        pttRuntimeRef.current.audioSelectedDeviceId,
        pttRuntimeRef.current.pushToTalkActions.getStartedAt(),
      );
    },
    cancelBackendVoiceTurn: () => pttRuntimeRef.current.actions.cancelVoiceTurn(),
    submitVoiceTurn: async (recorded) => {
      try {
        await pttRuntimeRef.current.actions.finishVoiceTurn({
          blob: recorded.blob,
          filename: recorded.mimeType.includes('mp4') ? 'voice-turn.mp4' : 'voice-turn.webm',
          deviceId: pttRuntimeRef.current.audioSelectedDeviceId,
          durationMs: recorded.durationMs,
        });
      } finally {
        pttRuntimeRef.current.pushToTalkActions.markComplete();
      }
    },
    isInteractionBlocked: () => Boolean(pttRuntimeRef.current.voiceDisabledReason) || pttRuntimeRef.current.pushToTalkActive,
  }), []);

  useEffect(() => () => {
    pttLifecycle.dispose();
  }, [pttLifecycle]);

  const stopInteraction = useCallback(async (reason: string) => {
    await interruptAssistantSpeech(reason);
    if (isVoiceLifecycleState(state.topLevelState)) {
      await actions.cancelVoiceTurn();
      await pttLifecycle.pressCancel();
    }
  }, [actions, interruptAssistantSpeech, pttLifecycle, state.topLevelState]);

  const stopDisabled = !isVoiceLifecycleState(state.topLevelState)
    && state.topLevelState !== 'speaking'
    && !speech.pendingMessageId
    && !speech.activeMessageId;

  return {
    state,
    actions,
    audio,
    pushToTalk,
    speech,
    pttLifecycle,
    sendTypedTurn,
    stopInteraction,
    stopDisabled,
  };
}
