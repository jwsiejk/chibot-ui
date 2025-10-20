import PolicyBus from '../policy/PolicyBus.js';
import { emitVoiceEvent } from '../ui/Events.js';
import { InteractionPolicyMode } from '../policy/InteractionPolicy.js';

const BLOCK_REASON = 'assistant_speaking';

const isManualOnlyDuringTts = (policy) => {
  if (!policy || typeof policy !== 'object') {
    return false;
  }
  const mode = policy.mode;
  return mode === InteractionPolicyMode.MANUAL_ONLY_DURING_TTS;
};

export function shouldAllowAutoVAD(policySnapshot = PolicyBus.getPolicy()) {
  const policy = policySnapshot;
  if (!policy || typeof policy !== 'object') {
    return true;
  }
  if (isManualOnlyDuringTts(policy)) {
    return policy.allow_auto_vad !== false;
  }
  if (Object.prototype.hasOwnProperty.call(policy, 'allow_auto_vad')) {
    return policy.allow_auto_vad !== false;
  }
  return true;
}

export function guardBargeInDispatch(src) {
  const policy = PolicyBus.getPolicy();
  if (shouldAllowAutoVAD(policy)) {
    return true;
  }
  const mode = policy && typeof policy === 'object' ? policy.mode : null;
  emitVoiceEvent('barge_in:blocked', { src, reason: BLOCK_REASON, mode });
  return false;
}

export default {
  shouldAllowAutoVAD,
  guardBargeInDispatch,
};
