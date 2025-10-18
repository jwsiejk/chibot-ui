import { pget as policyGet, POLICY_NOT_SET } from '../policy/index.js';

export const POLICY_SUPPRESS_ALL = 'all';
export const POLICY_SUPPRESS_NONE = 'none';
export const POLICY_SUPPRESS_GREET_ONLY = 'greet_only';

const normalizeStringPolicy = (value, fallback) => {
  if (typeof value === 'string') {
    const trimmed = value.trim().toLowerCase();
    if (trimmed) {
      return trimmed;
    }
  }
  return fallback;
};

export const policyString = (path, fallback) => {
  const value = policyGet(path, fallback);
  return normalizeStringPolicy(value, fallback);
};

export const policyBoolean = (path, fallback) => {
  const value = policyGet(path, POLICY_NOT_SET);
  if (value === POLICY_NOT_SET) {
    return fallback;
  }
  return value === true;
};

export const policyAllowLocalVad = () => (
  policyBoolean('voice_runtime.barge_in.allow_local_vad', true)
);

export const isGreetingDetail = (detail = {}) => {
  try {
    if (!detail || typeof detail !== 'object') {
      return false;
    }

    if (detail.prime === true || detail?.channel?.prime === true) {
      return true;
    }

    const tokens = [
      detail.phase,
      detail?.channel?.phase,
      detail.state,
      detail.label,
      detail.type,
    ].map((value) => (typeof value === 'string' ? value.toLowerCase() : ''))
      .filter(Boolean);

    return tokens.some((token) => token.includes('greet') || token.includes('welcome'));
  } catch {
    return false;
  }
};

export const resolveSuppressionMode = (detail = {}) => {
  const raw = policyString('voice_runtime.barge_in.suppress_during_tts', POLICY_SUPPRESS_ALL);
  if (raw === POLICY_SUPPRESS_NONE) {
    return POLICY_SUPPRESS_NONE;
  }
  if (raw === POLICY_SUPPRESS_GREET_ONLY) {
    return isGreetingDetail(detail) ? POLICY_SUPPRESS_ALL : POLICY_SUPPRESS_NONE;
  }
  return POLICY_SUPPRESS_ALL;
};

export default {
  POLICY_SUPPRESS_ALL,
  POLICY_SUPPRESS_NONE,
  POLICY_SUPPRESS_GREET_ONLY,
  policyString,
  policyBoolean,
  policyAllowLocalVad,
  isGreetingDetail,
  resolveSuppressionMode,
};
