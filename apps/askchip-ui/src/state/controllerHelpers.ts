import type { AskChipEvent, TranscriptMessage, TurnState } from '../types/contract';

export const MAX_RECENT_EVENTS = 24;
export const MAX_RECENT_TIMINGS = 12;
export const CONTRACT_TURN_STATES: TurnState[] = ['ready', 'listening', 'transcribing', 'thinking', 'error'];

export interface VoiceDraftState {
  mode: 'listening' | 'transcribing';
  text: string;
  durationMs: number | null;
}

export function dedupeEvents<T extends { id: string }>(events: T[]): T[] {
  const seen = new Set<string>();
  return events.filter((event) => {
    if (seen.has(event.id)) {
      return false;
    }
    seen.add(event.id);
    return true;
  });
}

export function isTurnState(value: unknown): value is TurnState {
  return typeof value === 'string' && CONTRACT_TURN_STATES.includes(value as TurnState);
}

export function buildListeningDraft(startedAt: number | null, now = Date.now()): VoiceDraftState {
  return {
    mode: 'listening',
    text: 'Listening… release to stop capture and submit this voice turn for transcription.',
    durationMs: startedAt ? Math.max(0, now - startedAt) : null,
  };
}

export function buildTranscribingDraft(durationMs: number | null): VoiceDraftState {
  return {
    mode: 'transcribing',
    text: 'Transcribing the released voice turn with faster-whisper before committing the canonical user message.',
    durationMs,
  };
}

function placeholderAssistantMessage(event: AskChipEvent, messageId: string): TranscriptMessage {
  const model = typeof event.payload.model === 'string' ? event.payload.model : undefined;

  return {
    id: messageId,
    session_id: event.session_id ?? '',
    role: 'assistant',
    source: 'model_output',
    modality: 'text',
    status: 'streaming',
    text: '',
    created_at: event.created_at,
    committed_at: null,
    completed_at: null,
    metadata: model ? { model } : {},
  };
}

export function applyAssistantStreamEvent(messages: TranscriptMessage[], event: AskChipEvent): TranscriptMessage[] {
  const messageId = typeof event.payload.message_id === 'string' ? event.payload.message_id : null;
  if (!messageId) {
    return messages;
  }

  const existingIndex = messages.findIndex((message) => message.id === messageId);
  const existingMessage = existingIndex >= 0 ? messages[existingIndex] : placeholderAssistantMessage(event, messageId);

  let nextMessage = existingMessage;
  if (event.type === 'assistant.started') {
    nextMessage = {
      ...existingMessage,
      session_id: event.session_id ?? existingMessage.session_id,
      status: 'streaming',
      metadata: typeof event.payload.model === 'string'
        ? { ...existingMessage.metadata, model: event.payload.model }
        : existingMessage.metadata,
    };
  }

  if (event.type === 'assistant.delta') {
    const delta = typeof event.payload.delta === 'string' ? event.payload.delta : '';
    if (!delta) {
      return messages;
    }
    nextMessage = {
      ...existingMessage,
      session_id: event.session_id ?? existingMessage.session_id,
      status: 'streaming',
      text: `${existingMessage.text}${delta}`,
    };
  }

  if (nextMessage === existingMessage && existingIndex >= 0) {
    return messages;
  }

  if (existingIndex >= 0) {
    return messages.map((message, index) => (index === existingIndex ? nextMessage : message));
  }

  return [...messages, nextMessage];
}


export function isVoiceLifecycleState(state: TurnState | null): boolean {
  return state === 'listening' || state === 'transcribing';
}

export function getSendingDisabledReason(params: {
  currentSessionId: string | null;
  pendingTurn: boolean;
  topLevelState: TurnState | null;
}): string | null {
  if (!params.currentSessionId) {
    return 'Create or select a session to start a typed chat.';
  }
  if (params.pendingTurn || params.topLevelState === 'thinking') {
    return 'Assistant is processing the current typed turn.';
  }
  if (isVoiceLifecycleState(params.topLevelState)) {
    return 'Release the active push-to-talk capture before sending a typed turn.';
  }
  return null;
}

export function getVoiceDisabledReason(params: {
  currentSessionId: string | null;
  pendingTurn: boolean;
  topLevelState: TurnState | null;
}): string | null {
  if (!params.currentSessionId) {
    return 'Create or select a session to start push-to-talk.';
  }
  if (params.pendingTurn || params.topLevelState === 'thinking') {
    return 'Wait for the current assistant turn to finish before recording another voice turn.';
  }
  if (isVoiceLifecycleState(params.topLevelState)) {
    return 'Finish the active push-to-talk lifecycle before starting another voice turn.';
  }
  return null;
}

export function getRecoveredVoiceTopLevelState(state: TurnState | null): TurnState {
  return state === 'thinking' ? 'thinking' : 'ready';
}
