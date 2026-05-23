import { evaluateClonedVoiceReadiness } from './clonedVoiceReadiness';
import { CLONED_CHAPPY_PROVIDER_LABEL, type ClonedVoiceConfig } from './clonedVoiceConfig';

export type VoiceProviderSelectionStatus = {
  selected_provider: 'standard' | 'cloned_chappy';
  active_provider_label: string;
  cloned_voice_ready: boolean;
  provider_adapter_available: boolean;
  reasons: string[];
  standard_voice_active: boolean;
  cloned_voice_status_label: 'Not configured' | 'Missing provider config' | 'Consent required' | 'Published profile required' | 'Ready for provider adapter';
};

const toStatusLabel = (ready: boolean, reasons: string[]): VoiceProviderSelectionStatus['cloned_voice_status_label'] => {
  if (ready) return 'Ready for provider adapter';
  if (reasons.includes('not_configured')) return 'Not configured';
  if (reasons.includes('consent_required')) return 'Consent required';
  if (reasons.includes('published_profile_required')) return 'Published profile required';
  return 'Missing provider config';
};

export const getVoiceProviderSelection = (input: {
  clonedVoiceConfig?: ClonedVoiceConfig | null;
  providerAdapterAvailable?: boolean;
}): VoiceProviderSelectionStatus => {
  const readiness = evaluateClonedVoiceReadiness(input.clonedVoiceConfig);
  const clonedVoiceReady = readiness.cloned_voice_ready;
  const providerAdapterAvailable = input.providerAdapterAvailable ?? false;
  const useClonedProvider = clonedVoiceReady && providerAdapterAvailable;

  return {
    selected_provider: useClonedProvider ? 'cloned_chappy' : 'standard',
    active_provider_label: useClonedProvider ? CLONED_CHAPPY_PROVIDER_LABEL : 'Standard voice',
    cloned_voice_ready: clonedVoiceReady,
    provider_adapter_available: providerAdapterAvailable,
    reasons: readiness.reasons,
    standard_voice_active: !useClonedProvider,
    cloned_voice_status_label: toStatusLabel(clonedVoiceReady, readiness.reasons),
  };
};
