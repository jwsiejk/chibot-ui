export type TtsSynthesisInput = {
  text: string;
  session_id: string;
  message_id: string;
  voice_profile_id?: string | null;
};

export type TtsSynthesisOutput = {
  audio_status: 'fallback_placeholder';
  provider_id: string;
  provider_label: string;
  spoken_text: string;
  audio_url: null;
};

export type TtsProvider = {
  provider_id: string;
  provider_label: string;
  synthesize: (input: TtsSynthesisInput) => TtsSynthesisOutput;
};
