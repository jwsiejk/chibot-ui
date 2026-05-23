import { describe, expect, it } from 'vitest';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { fallbackTtsProvider } from '../voice/fallbackTtsProvider';
import { getPublishedVoiceProfile, synthesizeAssistantTranscriptMessage } from '../voice/voiceRuntime';

const assistantMessage = (sessionId: string, text: string): TranscriptMessage => ({
  id: 'msg_assistant_1',
  ts: new Date().toISOString(),
  role: 'assistant',
  text,
  source: 'assistant_stream',
  session_id: sessionId,
  meta: {},
});

describe('voice runtime', () => {
  it('fallback provider conforms to TTS provider interface and preserves spoken text exactly', () => {
    const output = fallbackTtsProvider.synthesize({
      text: 'Exact text, punctuation intact!',
      session_id: 'session_1',
      message_id: 'msg_1',
      voice_profile_id: null,
    });

    expect(fallbackTtsProvider.provider_id).toBeTruthy();
    expect(fallbackTtsProvider.provider_label).toBeTruthy();
    expect(output.provider_id).toBe(fallbackTtsProvider.provider_id);
    expect(output.provider_label).toBe(fallbackTtsProvider.provider_label);
    expect(output.spoken_text).toBe('Exact text, punctuation intact!');
  });

  it('synthesizes only from assistant transcript text with exact spoken_text', () => {
    const output = synthesizeAssistantTranscriptMessage({
      session_id: 'session_1',
      message: assistantMessage('session_1', 'Do not rewrite this sentence.'),
    });

    expect(output.spoken_text).toBe('Do not rewrite this sentence.');
    expect(output.provider_id).toBe('local_fallback_tts');
  });

  it('rejects non-assistant transcript messages', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: { ...assistantMessage('session_1', 'x'), role: 'user' },
      }),
    ).toThrowError('TTS requires assistant transcript messages.');
  });

  it('rejects empty assistant transcript text', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: assistantMessage('session_1', '   '),
      }),
    ).toThrowError('TTS requires non-empty assistant transcript text.');
  });

  it('rejects mismatched session_id', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_2',
        message: assistantMessage('session_1', 'session mismatch'),
      }),
    ).toThrowError('TTS requires session_id to match transcript message session_id.');
  });

  it('rejects transcript payloads that include content instead of canonical text-only contract', () => {
    const message = { ...assistantMessage('session_1', 'no content field'), content: 'bad' } as unknown as TranscriptMessage;
    expect(() => synthesizeAssistantTranscriptMessage({ session_id: 'session_1', message })).toThrowError(
      'Invalid transcript message: content field is not allowed. Use text.',
    );
  });

  it('selects fallback path when no published voice profile exists', () => {
    expect(getPublishedVoiceProfile([])).toBeNull();
    expect(getPublishedVoiceProfile([{ id: 'voice_1', state: 'approved' }])).toBeNull();
  });
});
