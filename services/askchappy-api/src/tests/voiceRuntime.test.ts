import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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

const VOICE_MODULES = [
  '../voice/ttsProvider.ts',
  '../voice/fallbackTtsProvider.ts',
  '../voice/voiceRuntime.ts',
] as const;

const FORBIDDEN_VOICE_ASSET_PATTERNS = [
  '.wav',
  '.mp3',
  '.m4a',
  '.ogg',
  '.flac',
  '.webm',
  '.bin',
  '.pt',
  '.ckpt',
  '.onnx',
  '.npy',
  '.npz',
  '.pkl',
  '.emb',
  'embedding',
  '/assets/voice',
  '/assets/avatar',
] as const;

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
    expect(output.audio_status).toBe('fallback_placeholder');
    expect(output.audio_url).toBeNull();
  });

  it('synthesizes only from assistant transcript text with exact spoken_text', () => {
    const output = synthesizeAssistantTranscriptMessage({
      session_id: 'session_1',
      message: assistantMessage('session_1', 'Do not rewrite this sentence.'),
    });

    expect(output.spoken_text).toBe('Do not rewrite this sentence.');
    expect(output.provider_id).toBe('local_fallback_tts');
    expect(output.audio_status).toBe('fallback_placeholder');
    expect(output.audio_url).toBeNull();
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

  it('selects published voice profile and excludes non-published states', () => {
    const profiles = [
      { id: 'voice_draft', state: 'draft' },
      { id: 'voice_testing', state: 'testing' },
      { id: 'voice_approved', state: 'approved' },
      { id: 'voice_published', state: 'published' },
      { id: 'voice_disabled', state: 'disabled' },
    ] as const;

    expect(getPublishedVoiceProfile(profiles)).toEqual({ id: 'voice_published', state: 'published' });
    expect(getPublishedVoiceProfile([{ id: 'voice_1', state: 'approved' }])).toBeNull();
    expect(getPublishedVoiceProfile([{ id: 'voice_2', state: 'draft' }])).toBeNull();
    expect(getPublishedVoiceProfile([{ id: 'voice_3', state: 'testing' }])).toBeNull();
    expect(getPublishedVoiceProfile([{ id: 'voice_4', state: 'disabled' }])).toBeNull();
  });

  it('enforces voice asset safety by avoiding private audio/model/embedding artifacts in voice modules', () => {
    const moduleContents = VOICE_MODULES.map((modulePath) =>
      readFileSync(resolve(__dirname, modulePath), 'utf8').toLowerCase(),
    );

    FORBIDDEN_VOICE_ASSET_PATTERNS.forEach((forbiddenPattern) => {
      moduleContents.forEach((content) => {
        expect(content.includes(forbiddenPattern)).toBe(false);
      });
    });
  });
});
