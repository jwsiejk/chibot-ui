import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import { fallbackTtsProvider } from '../voice/fallbackTtsProvider';
import { evaluateClonedVoiceReadiness } from '../voice/clonedVoiceReadiness';
import { CLONED_CHAPPY_PROVIDER_KIND, CLONED_CHAPPY_PROVIDER_LABEL, type ClonedVoiceConfig } from '../voice/clonedVoiceConfig';
import { getVoiceProviderSelection } from '../voice/voiceProviderSelection';
import { getPublishedVoiceProfile, synthesizeAssistantTranscriptMessage } from '../voice/voiceRuntime';

const assistantMessage = (sessionId: string, text: string): TranscriptMessage => ({ id: 'msg_assistant_1', ts: new Date().toISOString(), role: 'assistant', text, source: 'assistant_stream', session_id: sessionId, meta: {} });

const baseConfig: ClonedVoiceConfig = {
  provider_kind: CLONED_CHAPPY_PROVIDER_KIND,
  provider_label: CLONED_CHAPPY_PROVIDER_LABEL,
  profile_id: 'profile_local_placeholder',
  endpoint: 'http://localhost:4000/cloned-voice-placeholder',
  auth_configured: true,
  consent_confirmed: true,
  publication_state: 'published',
  enabled: true,
};

describe('voice runtime', () => {
  it('standard voice provider is active by default and preserves spoken text exactly', () => {
    const output = fallbackTtsProvider.synthesize({ text: 'Exact text, punctuation intact!', session_id: 'session_1', message_id: 'msg_1', voice_profile_id: null });
    expect(output.provider_label).toBe('Standard voice');
    expect(output.spoken_text).toBe('Exact text, punctuation intact!');
  });

  it('synthesizes only from assistant transcript text with exact spoken_text', () => {
    const output = synthesizeAssistantTranscriptMessage({ session_id: 'session_1', message: assistantMessage('session_1', 'Do not rewrite this sentence.') });
    expect(output.spoken_text).toBe('Do not rewrite this sentence.');
  });

  it('rejects content field and non-assistant messages', () => {
    const message = { ...assistantMessage('session_1', 'no content field'), content: 'bad' } as unknown as TranscriptMessage;
    expect(() => synthesizeAssistantTranscriptMessage({ session_id: 'session_1', message })).toThrowError('Invalid transcript message: content field is not allowed. Use text.');
    expect(() => synthesizeAssistantTranscriptMessage({ session_id: 'session_1', message: { ...assistantMessage('session_1', 'x'), role: 'user' } })).toThrowError('TTS requires assistant transcript messages.');
  });

  it('selects only published cloned voice profile', () => {
    expect(getPublishedVoiceProfile([{ id: 'voice_1', state: 'approved' }])).toBeNull();
    expect(getPublishedVoiceProfile([{ id: 'voice_published', state: 'published' }])).toEqual({ id: 'voice_published', state: 'published' });
  });

  it('standard voice selected by default and when cloned config is missing', () => {
    expect(getVoiceProviderSelection({ clonedVoiceConfig: null }).selected_provider).toBe('standard');
    expect(getVoiceProviderSelection({}).selected_provider).toBe('standard');
  });

  it.each(['draft', 'testing', 'approved', 'disabled'] as const)('non-published state %s selects standard', (state) => {
    const selection = getVoiceProviderSelection({ clonedVoiceConfig: { ...baseConfig, publication_state: state } });
    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toContain('published_profile_required');
  });

  it('published profile with missing endpoint/config selects standard', () => {
    const selection = getVoiceProviderSelection({ clonedVoiceConfig: { ...baseConfig, endpoint: '', auth_configured: false } });
    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toEqual(expect.arrayContaining(['missing_provider_endpoint', 'missing_provider_config']));
  });

  it('published profile with consent false or enabled false selects standard', () => {
    expect(getVoiceProviderSelection({ clonedVoiceConfig: { ...baseConfig, consent_confirmed: false } }).selected_provider).toBe('standard');
    expect(getVoiceProviderSelection({ clonedVoiceConfig: { ...baseConfig, enabled: false } }).selected_provider).toBe('standard');
  });

  it('published complete config and consent true is ready for provider adapter', () => {
    const selection = getVoiceProviderSelection({ clonedVoiceConfig: baseConfig });
    expect(selection.selected_provider).toBe('cloned_chappy');
    expect(selection.cloned_voice_ready).toBe(true);
    expect(selection.cloned_voice_status_label).toBe('Ready for provider adapter');
  });

  it('readiness includes reasons and never claims cloned active with errors', () => {
    const readiness = evaluateClonedVoiceReadiness({ ...baseConfig, enabled: false, consent_confirmed: false });
    expect(readiness.cloned_voice_ready).toBe(false);
    expect(readiness.reasons).toEqual(expect.arrayContaining(['provider_disabled', 'consent_required']));
    expect(getVoiceProviderSelection({ clonedVoiceConfig: { ...baseConfig, enabled: false } }).selected_provider).toBe('standard');
  });

  it('enforces asset safety in voice modules', () => {
    const files = ['../voice/ttsProvider.ts', '../voice/fallbackTtsProvider.ts', '../voice/voiceRuntime.ts', '../voice/clonedVoiceConfig.ts', '../voice/clonedVoiceReadiness.ts', '../voice/voiceProviderSelection.ts'] as const;
    const content = files.map((file) => readFileSync(resolve(__dirname, file), 'utf8').toLowerCase()).join('\n');
    expect(content).not.toMatch(/\.(wav|mp3|m4a|ogg|flac|webm|bin|pt|ckpt|onnx|npy|npz|pkl|emb)/);
    expect(content).not.toContain('elevenlabs');
  });
});
