import { isVoiceProfileState } from '../../../../shared/contracts/voice';
import {
  CLONED_CHAPPY_PROVIDER_KIND,
  CLONED_CHAPPY_PROVIDER_LABEL,
  type ClonedVoiceConfig,
} from './clonedVoiceConfig';

export type ClonedVoiceReadiness = {
  cloned_voice_ready: boolean;
  reasons: string[];
  provider_adapter_ready: boolean;
};

export const evaluateClonedVoiceReadiness = (config: ClonedVoiceConfig | null | undefined): ClonedVoiceReadiness => {
  const reasons: string[] = [];

  if (!config) {
    reasons.push('not_configured');
    return { cloned_voice_ready: false, reasons, provider_adapter_ready: false };
  }

  if (config.provider_kind !== CLONED_CHAPPY_PROVIDER_KIND) reasons.push('invalid_provider_kind');
  if (config.provider_label !== CLONED_CHAPPY_PROVIDER_LABEL) reasons.push('invalid_provider_label');
  if (!config.profile_id.trim()) reasons.push('missing_profile_id');
  if (!config.endpoint.trim()) reasons.push('missing_provider_endpoint');
  if (!config.auth_configured) reasons.push('missing_provider_config');
  if (!config.enabled) reasons.push('provider_disabled');
  if (!isVoiceProfileState(config.publication_state)) reasons.push('invalid_publication_state');
  if (config.publication_state !== 'published') reasons.push('published_profile_required');
  if (!config.consent_confirmed) reasons.push('consent_required');

  return {
    cloned_voice_ready: reasons.length === 0,
    reasons,
    provider_adapter_ready: reasons.length === 0,
  };
};
