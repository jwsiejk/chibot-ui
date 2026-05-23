import { evaluateClonedVoiceReadiness } from './clonedVoiceReadiness';
import { CLONED_CHAPPY_PROVIDER_LABEL, type ClonedVoiceConfig } from './clonedVoiceConfig';

export type VoiceProviderSelectionStatus = {
  selected_provider: 'standard' | 'cloned_chappy';
  active_provider_label: string;
  cloned_voice_ready: boolean;
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

export const getVoiceProviderSelection = (input: { clonedVoiceConfig?: ClonedVoiceConfig | null }): VoiceProviderSelectionStatus => {
  const readiness = evaluateClonedVoiceReadiness(input.clonedVoiceConfig);
  const clonedVoiceReady = readiness.cloned_voice_ready;

  return {
    selected_provider: clonedVoiceReady ? 'cloned_chappy' : 'standard',
    active_provider_label: clonedVoiceReady ? CLONED_CHAPPY_PROVIDER_LABEL : 'Standard voice',
    cloned_voice_ready: clonedVoiceReady,
    reasons: readiness.reasons,
    standard_voice_active: !clonedVoiceReady,
    cloned_voice_status_label: toStatusLabel(clonedVoiceReady, readiness.reasons),
  };
};
