import type { AskChipEvent, TranscriptMessage } from '../types/contract';

export const MAX_RECENT_EVENTS = 24;
export const MAX_RECENT_TIMINGS = 12;

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
