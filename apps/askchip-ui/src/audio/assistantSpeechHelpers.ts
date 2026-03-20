import type { TranscriptMessage } from '../types/contract';

export function hasSpeechStarted(message: TranscriptMessage): boolean {
  const speech = message.metadata?.speech;
  return typeof speech === 'object' && speech !== null && 'last_started_at' in speech;
}

export function findNextSpeechMessage(messages: TranscriptMessage[]): TranscriptMessage | null {
  const assistants = [...messages].reverse().filter((message) => message.role === 'assistant' && message.status === 'completed' && message.text.trim());
  return assistants.find((message) => !hasSpeechStarted(message)) ?? null;
}
