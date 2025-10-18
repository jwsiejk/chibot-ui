import { DEFAULT_CONFIG } from './core/index.js';

let hasWarned = false;

const warnOnce = (message) => {
  if (hasWarned) {
    return;
  }
  hasWarned = true;
  console.warn(`[voice][config] ${message}`);
};

const clampNumber = (target, key, { min = 0, fallback, label }) => {
  if (!target) {
    return;
  }

  const raw = target[key];
  const next = Number(raw);

  if (!Number.isFinite(next) || next < min) {
    target[key] = Math.max(min, fallback);
    warnOnce(`Adjusted ${label} from ${raw} to ${target[key]}`);
  } else {
    target[key] = next;
  }
};

const ensureBoolean = (target, key, { fallback, label }) => {
  if (!target) {
    return;
  }

  if (typeof target[key] !== 'boolean') {
    target[key] = fallback;
    warnOnce(`Adjusted ${label} to default (${fallback})`);
  }
};

export function validateConfig(config) {
  if (!config || typeof config !== 'object') {
    warnOnce('Received invalid config, using defaults');
    return DEFAULT_CONFIG;
  }

  const result = {
    ...config,
    commit: { ...DEFAULT_CONFIG.commit, ...config.commit },
    tts: { ...DEFAULT_CONFIG.tts, ...config.tts },
    shadow: { ...DEFAULT_CONFIG.shadow, ...config.shadow },
    evidence: { ...DEFAULT_CONFIG.evidence, ...config.evidence },
    metrics: { ...DEFAULT_CONFIG.metrics, ...config.metrics },
    dual_vad: { ...DEFAULT_CONFIG.dual_vad, ...config.dual_vad },
  };

  clampNumber(result.commit, 'min_ms', {
    min: 0,
    fallback: DEFAULT_CONFIG.commit.min_ms,
    label: 'commit.min_ms',
  });

  clampNumber(result.commit, 'no_partial_timeout_ms', {
    min: result.commit.min_ms,
    fallback: DEFAULT_CONFIG.commit.no_partial_timeout_ms,
    label: 'commit.no_partial_timeout_ms',
  });

  ensureBoolean(result.commit, 'drop_if_no_partial', {
    fallback: DEFAULT_CONFIG.commit.drop_if_no_partial,
    label: 'commit.drop_if_no_partial',
  });

  clampNumber(result.tts, 'decay_ms', {
    min: 0,
    fallback: DEFAULT_CONFIG.tts.decay_ms,
    label: 'tts.decay_ms',
  });

  clampNumber(result.shadow, 'ms', {
    min: 0,
    fallback: DEFAULT_CONFIG.shadow.ms,
    label: 'shadow.ms',
  });

  clampNumber(result.evidence, 'snr_sigma', {
    min: 0,
    fallback: DEFAULT_CONFIG.evidence.snr_sigma,
    label: 'evidence.snr_sigma',
  });

  clampNumber(result.evidence, 'asr_conf', {
    min: 0,
    fallback: DEFAULT_CONFIG.evidence.asr_conf,
    label: 'evidence.asr_conf',
  });

  clampNumber(result.evidence, 'threshold', {
    min: 0,
    fallback: DEFAULT_CONFIG.evidence.threshold,
    label: 'evidence.threshold',
  });

  ensureBoolean(result.metrics, 'client_enabled', {
    fallback: DEFAULT_CONFIG.metrics.client_enabled,
    label: 'metrics.client_enabled',
  });

  ensureBoolean(result.metrics, 'server_enabled', {
    fallback: DEFAULT_CONFIG.metrics.server_enabled,
    label: 'metrics.server_enabled',
  });

  ensureBoolean(result.dual_vad, 'enabled', {
    fallback: DEFAULT_CONFIG.dual_vad.enabled,
    label: 'dual_vad.enabled',
  });

  clampNumber(result.dual_vad, 'commit_conf', {
    min: 0,
    fallback: DEFAULT_CONFIG.dual_vad.commit_conf,
    label: 'dual_vad.commit_conf',
  });

  clampNumber(result.dual_vad, 'asr_stale_ms', {
    min: 0,
    fallback: DEFAULT_CONFIG.dual_vad.asr_stale_ms,
    label: 'dual_vad.asr_stale_ms',
  });

  clampNumber(result.dual_vad, 'close_quiet_ms', {
    min: 0,
    fallback: DEFAULT_CONFIG.dual_vad.close_quiet_ms,
    label: 'dual_vad.close_quiet_ms',
  });

  return result;
}

export * from './core/index.js';
export * from './io/index.js';
export * from './policy/index.js';
export * from './ui/index.js';
export * from './utils/policy.js';
