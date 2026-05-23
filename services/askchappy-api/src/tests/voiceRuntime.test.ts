import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { TranscriptMessage } from '../../../../shared/contracts/transcript';
import {
  CLONED_CHAPPY_PROVIDER_KIND,
  CLONED_CHAPPY_PROVIDER_LABEL,
  type ClonedVoiceConfig,
} from '../voice/clonedVoiceConfig';
import { evaluateClonedVoiceReadiness } from '../voice/clonedVoiceReadiness';
import { fallbackTtsProvider } from '../voice/fallbackTtsProvider';
import { getVoiceProviderSelection } from '../voice/voiceProviderSelection';
import {
  getPublishedVoiceProfile,
  synthesizeAssistantTranscriptMessage,
} from '../voice/voiceRuntime';

const createAssistantMessage = (
  sessionId: string,
  text: string,
): TranscriptMessage => ({
  id: 'msg_assistant_1',
  ts: new Date().toISOString(),
  role: 'assistant',
  text,
  source: 'assistant_stream',
  session_id: sessionId,
  meta: {},
});

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
  it('standard voice provider conforms to TTS provider fallback contract', () => {
    const output = fallbackTtsProvider.synthesize({
      text: 'Exact text, punctuation intact!',
      session_id: 'session_1',
      message_id: 'msg_1',
      voice_profile_id: null,
    });

    expect(output.provider_id).toBe('local_fallback_tts');
    expect(output.provider_label).toBe('Standard voice');
    expect(output.spoken_text).toBe('Exact text, punctuation intact!');
    expect(output.audio_status).toBe('fallback_placeholder');
    expect(output.audio_url).toBeNull();
  });

  it('assistant transcript text synthesis preserves spoken_text exactly', () => {
    const output = synthesizeAssistantTranscriptMessage({
      session_id: 'session_1',
      message: createAssistantMessage('session_1', 'Do not rewrite this sentence.'),
    });

    expect(output.spoken_text).toBe('Do not rewrite this sentence.');
  });

  it('non-assistant messages are rejected', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: {
          ...createAssistantMessage('session_1', 'x'),
          role: 'user',
        },
      }),
    ).toThrowError('TTS requires assistant transcript messages.');
  });

  it('empty assistant text is rejected', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: createAssistantMessage('session_1', '   '),
      }),
    ).toThrowError('TTS requires non-empty assistant transcript text.');
  });

  it('mismatched session_id is rejected', () => {
    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: createAssistantMessage('session_2', 'Valid content'),
      }),
    ).toThrowError('TTS requires session_id to match transcript message session_id.');
  });

  it('content field payload is rejected', () => {
    const message = {
      ...createAssistantMessage('session_1', 'no content field'),
      content: 'bad',
    } as unknown as TranscriptMessage;

    expect(() =>
      synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message,
      }),
    ).toThrowError(
      'Invalid transcript message: content field is not allowed. Use text.',
    );
  });

  it('published profile selection works', () => {
    const selected = getPublishedVoiceProfile([
      { id: 'voice_published', state: 'published' },
    ]);

    expect(selected).toEqual({ id: 'voice_published', state: 'published' });
  });

  it.each(['draft', 'testing', 'approved', 'disabled'] as const)(
    'non-published states are not selected (%s)',
    (state) => {
      expect(getPublishedVoiceProfile([{ id: `voice_${state}`, state }])).toBeNull();
    },
  );

  it('missing config => standard', () => {
    expect(getVoiceProviderSelection({ clonedVoiceConfig: null }).selected_provider).toBe(
      'standard',
    );
    expect(getVoiceProviderSelection({}).selected_provider).toBe('standard');
  });

  it.each(['draft', 'testing', 'approved', 'disabled'] as const)(
    '%s => standard',
    (publication_state) => {
      const selection = getVoiceProviderSelection({
        clonedVoiceConfig: {
          ...baseConfig,
          publication_state,
        },
      });

      expect(selection.selected_provider).toBe('standard');
      expect(selection.reasons).toContain('published_profile_required');
    },
  );

  it('missing endpoint/auth => standard with reasons', () => {
    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: {
        ...baseConfig,
        endpoint: '',
        auth_configured: false,
      },
    });

    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toEqual(
      expect.arrayContaining([
        'missing_provider_endpoint',
        'missing_provider_config',
      ]),
    );
  });

  it('consent false => standard', () => {
    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: {
        ...baseConfig,
        consent_confirmed: false,
      },
    });

    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toContain('consent_required');
  });

  it('enabled false => standard', () => {
    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: {
        ...baseConfig,
        enabled: false,
      },
    });

    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toContain('provider_disabled');
  });

  it('incomplete config => standard', () => {
    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: {
        ...baseConfig,
        provider_label: '',
      },
    });

    expect(selection.selected_provider).toBe('standard');
    expect(selection.reasons).toContain('invalid_provider_label');
  });

  it('complete published config + consent => cloned readiness is true / Ready for provider adapter', () => {
    const selection = getVoiceProviderSelection({ clonedVoiceConfig: baseConfig });

    expect(selection.selected_provider).toBe('cloned_chappy');
    expect(selection.cloned_voice_ready).toBe(true);
    expect(selection.cloned_voice_status_label).toBe('Ready for provider adapter');
  });

  it('readiness never claims cloned active with errors', () => {
    const readiness = evaluateClonedVoiceReadiness({
      ...baseConfig,
      enabled: false,
      consent_confirmed: false,
    });

    expect(readiness.cloned_voice_ready).toBe(false);
    expect(readiness.reasons).toEqual(
      expect.arrayContaining(['provider_disabled', 'consent_required']),
    );

    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: {
        ...baseConfig,
        enabled: false,
      },
    });
    expect(selection.selected_provider).toBe('standard');
  });

  it('asset-safety checks include audio/model/embedding/avatar path patterns', () => {
    const files = [
      '../voice/ttsProvider.ts',
      '../voice/fallbackTtsProvider.ts',
      '../voice/voiceRuntime.ts',
      '../voice/clonedVoiceConfig.ts',
      '../voice/clonedVoiceReadiness.ts',
      '../voice/voiceProviderSelection.ts',
    ] as const;

    const content = files
      .map((file) => readFileSync(resolve(__dirname, file), 'utf8').toLowerCase())
      .join('\n');

    expect(content).not.toMatch(
      /\.(wav|mp3|m4a|ogg|flac|webm|bin|pt|ckpt|onnx|npy|npz|pkl|emb)/,
    );
    expect(content).not.toMatch(/avatar\/(samples|uploads|models|embeddings)/);
    expect(content).not.toContain('elevenlabs');
  });
});
