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
