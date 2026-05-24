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
import { getKokoroTtsConfig } from '../voice/kokoroTtsConfig';
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
  it('standard voice provider returns unavailable when Kokoro is not configured', async () => {
    const output = await fallbackTtsProvider.synthesize({
      text: 'Exact text, punctuation intact!',
      session_id: 'session_1',
      message_id: 'msg_1',
      voice_profile_id: null,
    });

    expect(output.provider_id).toBe('local_fallback_tts');
    expect(output.provider_label).toBe('Standard voice');
    expect(output.spoken_text).toBe('Exact text, punctuation intact!');
    expect(output.audio_status).toBe('tts_unavailable');
    expect(output.audio_base64).toBeNull();
  });

  it('assistant transcript text synthesis preserves spoken_text exactly', async () => {
    const output = await synthesizeAssistantTranscriptMessage({
      session_id: 'session_1',
      message: createAssistantMessage('session_1', 'Do not rewrite this sentence.'),
    });

    expect(output.spoken_text).toBe('Do not rewrite this sentence.');
  });

  it('non-assistant messages are rejected', async () => {
    await expect(synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: {
          ...createAssistantMessage('session_1', 'x'),
          role: 'user',
        },
      }),
    ).rejects.toThrowError('TTS requires assistant transcript messages.');
  });

  it('empty assistant text is rejected', async () => {
    await expect(synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: createAssistantMessage('session_1', '   '),
      }),
    ).rejects.toThrowError('TTS requires non-empty assistant transcript text.');
  });

  it('mismatched session_id is rejected', async () => {
    await expect(synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message: createAssistantMessage('session_2', 'Valid content'),
      }),
    ).rejects.toThrowError('TTS requires session_id to match transcript message session_id.');
  });

  it('content field payload is rejected', async () => {
    const message = {
      ...createAssistantMessage('session_1', 'no content field'),
      content: 'bad',
    } as unknown as TranscriptMessage;

    await expect(synthesizeAssistantTranscriptMessage({
        session_id: 'session_1',
        message,
      }),
    ).rejects.toThrowError(
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

    expect(selection.selected_provider).toBe('standard');
    expect(selection.provider_adapter_available).toBe(false);
    expect(selection.standard_voice_active).toBe(true);
    expect(selection.cloned_voice_ready).toBe(true);
    expect(selection.cloned_voice_status_label).toBe('Ready for provider adapter');
  });


  it('selects cloned provider only when readiness passes and adapter is explicitly available', () => {
    const selection = getVoiceProviderSelection({
      clonedVoiceConfig: baseConfig,
      providerAdapterAvailable: true,
    });

    expect(selection.selected_provider).toBe('cloned_chappy');
    expect(selection.standard_voice_active).toBe(false);
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


it('kokoro config defaults are local-first', () => {
  const config = getKokoroTtsConfig({});
  expect(config.baseUrl).toBe('http://127.0.0.1:8880');
  expect(config.voice).toBe('af_sarah');
  expect(config.format).toBe('wav');
  expect(config.configured).toBe(false);
});

it('kokoro config supports overrides', () => {
  const config = getKokoroTtsConfig({
    KOKORO_TTS_BASE_URL: 'http://127.0.0.1:9999',
    KOKORO_TTS_VOICE: 'af_bella',
    KOKORO_TTS_FORMAT: 'mp3',
    KOKORO_TTS_TIMEOUT_MS: '12000',
  });
  expect(config).toMatchObject({
    baseUrl: 'http://127.0.0.1:9999',
    voice: 'af_bella',
    format: 'mp3',
    timeoutMs: 12000,
    configured: true,
  });
});
