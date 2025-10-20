import PolicyBus from '../policy/PolicyBus.js';
import { emitVoiceEvent } from '../ui/Events.js';
import { InteractionPolicyMode } from '../policy/InteractionPolicy.js';

const SUPPRESSED_REASON = 'manual_only_during_tts';

export function canOpenTurn() {
  const policy = PolicyBus.getPolicy();
  if (policy && policy.mode === InteractionPolicyMode.MANUAL_ONLY_DURING_TTS) {
    emitVoiceEvent('evidence:suppressed', { reason: SUPPRESSED_REASON, mode: policy.mode });
    return false;
  }
  return true;
}

export default {
  canOpenTurn,
};
