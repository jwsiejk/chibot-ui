export const TRANSCRIPT_ROLES = ['user', 'assistant', 'system'] as const;
export const TRANSCRIPT_SOURCES = [
  'typed',
  'voice',
  'assistant_stream',
  'speech',
  'system',
  'summary',
] as const;

export type TranscriptRole = (typeof TRANSCRIPT_ROLES)[number];
export type TranscriptSource = (typeof TRANSCRIPT_SOURCES)[number];

export type TranscriptMessage = {
  id: string;
  ts: string;
  role: TranscriptRole;
  text: string;
  source: TranscriptSource;
  session_id: string;
  meta: Record<string, unknown>;
};

export const isTranscriptRole = (value: unknown): value is TranscriptRole =>
  typeof value === 'string' && TRANSCRIPT_ROLES.includes(value as TranscriptRole);

export const isTranscriptSource = (value: unknown): value is TranscriptSource =>
  typeof value === 'string' && TRANSCRIPT_SOURCES.includes(value as TranscriptSource);

export const isTranscriptMessage = (value: unknown): value is TranscriptMessage => {
  if (!value || typeof value !== 'object') return false;
  const message = value as Record<string, unknown>;
  if ('content' in message) return false;

  return (
    typeof message.id === 'string' &&
    typeof message.ts === 'string' &&
    isTranscriptRole(message.role) &&
    typeof message.text === 'string' &&
    isTranscriptSource(message.source) &&
    typeof message.session_id === 'string' &&
    !!message.meta &&
    typeof message.meta === 'object'
  );
};
