const MODES = Object.freeze({
  MANUAL_ONLY_DURING_TTS: 'manual_only_during_tts',
  AUTO_VAD_READY: 'auto_vad_ready',
});

const SHAPE_KEYS = Object.freeze([
  'mode',
  'allow_auto_vad',
  'auto_commit_when_ready',
  'allow_ptt_barge',
  'suppress_vad_during_tts',
]);

function coerceBoolean(value, fallback = false) {
  if (typeof value === 'boolean') {
    return value;
  }
  if (value == null) {
    return fallback;
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true' || normalized === '1' || normalized === 'yes') {
      return true;
    }
    if (normalized === 'false' || normalized === '0' || normalized === 'no') {
      return false;
    }
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      return fallback;
    }
    return value !== 0;
  }
  return fallback;
}

function normalizeMode(rawMode, fallback) {
  const normalized = typeof rawMode === 'string' ? rawMode.trim().toLowerCase() : '';
  if (normalized === MODES.MANUAL_ONLY_DURING_TTS) {
    return MODES.MANUAL_ONLY_DURING_TTS;
  }
  if (normalized === MODES.AUTO_VAD_READY) {
    return MODES.AUTO_VAD_READY;
  }
  return fallback;
}

function baseShapeForMode(mode) {
  if (mode === MODES.MANUAL_ONLY_DURING_TTS) {
    return {
      mode,
      allow_auto_vad: false,
      auto_commit_when_ready: false,
      allow_ptt_barge: true,
      suppress_vad_during_tts: true,
    };
  }
  return {
    mode,
    allow_auto_vad: true,
    auto_commit_when_ready: false,
    allow_ptt_barge: true,
    suppress_vad_during_tts: false,
  };
}

function clonePolicy(policy) {
  if (!policy || typeof policy !== 'object') {
    return null;
  }
  const snapshot = {};
  for (const key of SHAPE_KEYS) {
    if (Object.prototype.hasOwnProperty.call(policy, key)) {
      snapshot[key] = policy[key];
    }
  }
  return snapshot;
}

export function ensureInteractionPolicy(rawPolicy, fallbackMode = MODES.AUTO_VAD_READY) {
  const baseMode = normalizeMode(rawPolicy?.mode, fallbackMode);
  const base = baseShapeForMode(baseMode);
  if (!rawPolicy || typeof rawPolicy !== 'object') {
    return Object.freeze({ ...base });
  }
  const normalized = { ...base };
  if (Object.prototype.hasOwnProperty.call(rawPolicy, 'allow_auto_vad')) {
    normalized.allow_auto_vad = coerceBoolean(rawPolicy.allow_auto_vad, base.allow_auto_vad);
  }
  if (Object.prototype.hasOwnProperty.call(rawPolicy, 'auto_commit_when_ready')) {
    normalized.auto_commit_when_ready = coerceBoolean(
      rawPolicy.auto_commit_when_ready,
      base.auto_commit_when_ready,
    );
  }
  if (Object.prototype.hasOwnProperty.call(rawPolicy, 'allow_ptt_barge')) {
    normalized.allow_ptt_barge = coerceBoolean(rawPolicy.allow_ptt_barge, base.allow_ptt_barge);
  }
  if (Object.prototype.hasOwnProperty.call(rawPolicy, 'suppress_vad_during_tts')) {
    normalized.suppress_vad_during_tts = coerceBoolean(
      rawPolicy.suppress_vad_during_tts,
      base.suppress_vad_during_tts,
    );
  }
  normalized.mode = baseMode;
  return Object.freeze(normalized);
}

export function cloneInteractionPolicy(rawPolicy) {
  const snapshot = clonePolicy(rawPolicy);
  if (!snapshot) {
    return Object.freeze(baseShapeForMode(MODES.AUTO_VAD_READY));
  }
  snapshot.mode = normalizeMode(snapshot.mode, MODES.AUTO_VAD_READY);
  return Object.freeze({ ...snapshot });
}

export function manualOnlyDuringTtsPolicy(overrides = {}) {
  const base = baseShapeForMode(MODES.MANUAL_ONLY_DURING_TTS);
  return ensureInteractionPolicy({ ...base, ...clonePolicy(overrides) }, MODES.MANUAL_ONLY_DURING_TTS);
}

export function autoVadReadyPolicy(overrides = {}) {
  const base = baseShapeForMode(MODES.AUTO_VAD_READY);
  return ensureInteractionPolicy({ ...base, ...clonePolicy(overrides) }, MODES.AUTO_VAD_READY);
}

export const InteractionPolicyMode = MODES;
