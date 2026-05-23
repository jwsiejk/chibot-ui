import type { VoiceProfileState } from '../../../../shared/contracts/voice';

export const CLONED_CHAPPY_PROVIDER_KIND = 'cloned_chappy';
export const CLONED_CHAPPY_PROVIDER_LABEL = 'Chappy cloned voice';

export type ClonedVoiceConfig = {
  provider_kind: typeof CLONED_CHAPPY_PROVIDER_KIND;
  provider_label: string;
  profile_id: string;
  endpoint: string;
  auth_configured: boolean;
  consent_confirmed: boolean;
  publication_state: VoiceProfileState;
  enabled: boolean;
};

export const getLocalClonedVoiceConfig = (): ClonedVoiceConfig | null => null;
