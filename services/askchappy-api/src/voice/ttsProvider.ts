export type TtsSynthesisInput = {
  text: string;
  session_id: string;
  message_id: string;
  voice_profile_id?: string | null;
};

export type TtsSynthesisOutput = {
  audio_status: 'ready' | 'tts_unavailable';
  provider_id: string;
  provider_label: string;
  spoken_text: string;
  audio_base64: string | null;
  audio_format: string | null;
  unavailable_reason?: 'not_configured' | 'runtime_unreachable';
  provider_meta?: Record<string, string | number | boolean | null>;
};

export type TtsProvider = {
  provider_id: string;
  provider_label: string;
  synthesize: (input: TtsSynthesisInput) => Promise<TtsSynthesisOutput>;
};
