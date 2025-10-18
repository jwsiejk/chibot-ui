const DEFAULT_POLICY = Object.freeze({
  voice_runtime: {
    confirm_window: {
      first_turn: {
        min_ms: 420,
        max_ms: 1200,
        until_asr_ready: true,
      },
      warm_turn: {
        min_ms: 420,
        max_ms: 1020,
        until_asr_ready: false,
      },
    },
    snr_threshold_db: {
      first_turn: 8.0,
      warm_turn: 8.0,
    },
    barge_in: {
      allow_ptt: true,
      allow_local_vad: true,
      require_asr_evidence: false,
      suppress_during_tts: 'all',
      post_tts_hold_ms: 200,
    },
    auto_commit: {
      enabled: true,
      requires_dual_evidence: false,
      asr_ready_required: false,
    },
  },
});

const POLICY_NOT_SET = Symbol('interaction-policy-not-set');

let cachedPolicy = null;
let pendingPolicy = null;
let loadedFromServer = false;

function deepClone(value) {
  if (value == null || typeof value !== 'object') {
    return value;
  }
  if (typeof globalThis.structuredClone === 'function') {
    try {
      return globalThis.structuredClone(value);
    } catch {}
  }
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
}

function deepMerge(target, source) {
  if (!target || typeof target !== 'object') {
    return target;
  }
  if (!source || typeof source !== 'object') {
    return target;
  }
  for (const [key, value] of Object.entries(source)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      if (!target[key] || typeof target[key] !== 'object' || Array.isArray(target[key])) {
        target[key] = {};
      }
      deepMerge(target[key], value);
    } else {
      target[key] = deepClone(value);
    }
  }
  return target;
}

function deepFreeze(target) {
  if (!target || typeof target !== 'object' || Object.isFrozen(target)) {
    return target;
  }
  Object.freeze(target);
  for (const value of Object.values(target)) {
    deepFreeze(value);
  }
  return target;
}

function normalizePolicy(raw) {
  const base = deepClone(DEFAULT_POLICY);
  if (raw && typeof raw === 'object') {
    deepMerge(base, raw);
  }
  return deepFreeze(base);
}

function readWindowPolicy() {
  try {
    if (typeof window !== 'undefined' && window.__askchip_policy) {
      return window.__askchip_policy;
    }
  } catch {}
  try {
    if (typeof globalThis !== 'undefined' && globalThis.__askchip_policy) {
      return globalThis.__askchip_policy;
    }
  } catch {}
  return null;
}

function storeWindowPolicy(policy) {
  const snapshot = deepClone(policy);
  try {
    if (typeof window !== 'undefined') {
      window.__askchip_policy = snapshot;
      return;
    }
  } catch {}
  try {
    if (typeof globalThis !== 'undefined') {
      globalThis.__askchip_policy = snapshot;
    }
  } catch {}
}

function resolveFetch() {
  if (typeof fetch === 'function') {
    return fetch.bind(globalThis);
  }
  if (typeof globalThis !== 'undefined' && typeof globalThis.fetch === 'function') {
    return globalThis.fetch.bind(globalThis);
  }
  return null;
}

function ensureCachedPolicy() {
  if (cachedPolicy) {
    return cachedPolicy;
  }
  const existing = readWindowPolicy();
  if (existing) {
    cachedPolicy = normalizePolicy(existing);
    loadedFromServer = true;
    return cachedPolicy;
  }
  cachedPolicy = normalizePolicy(DEFAULT_POLICY);
  return cachedPolicy;
}

export function getPolicySync() {
  return ensureCachedPolicy();
}

export function pget(path, fallback = undefined) {
  if (typeof path !== 'string' || !path.trim()) {
    return fallback;
  }
  const parts = path.split('.').filter(Boolean);
  if (!parts.length) {
    return fallback;
  }
  const policy = ensureCachedPolicy();
  let cursor = policy;
  for (const part of parts) {
    if (cursor && typeof cursor === 'object' && part in cursor) {
      cursor = cursor[part];
    } else {
      return fallback;
    }
  }
  return cursor === undefined ? fallback : cursor;
}

async function fetchPolicyFromServer() {
  const fetchFn = resolveFetch();
  if (!fetchFn) {
    return normalizePolicy(DEFAULT_POLICY);
  }
  const response = await fetchFn('/api/v1/policy/effective', {
    method: 'GET',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response || !response.ok) {
    throw new Error('Failed to fetch interaction policy');
  }
  const data = await response.json();
  return normalizePolicy(data);
}

export function ensurePolicy() {
  if (cachedPolicy && loadedFromServer && !pendingPolicy) {
    return Promise.resolve(cachedPolicy);
  }
  if (pendingPolicy) {
    return pendingPolicy;
  }
  const existing = readWindowPolicy();
  if (!cachedPolicy && existing) {
    cachedPolicy = normalizePolicy(existing);
    loadedFromServer = true;
    return Promise.resolve(cachedPolicy);
  }
  pendingPolicy = (async () => {
    try {
      const policy = await fetchPolicyFromServer();
      cachedPolicy = policy;
      loadedFromServer = true;
      storeWindowPolicy(policy);
      return policy;
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('[voice][policy] falling back to default policy', err);
      }
      const fallbackPolicy = ensureCachedPolicy();
      storeWindowPolicy(fallbackPolicy);
      loadedFromServer = false;
      return fallbackPolicy;
    } finally {
      pendingPolicy = null;
    }
  })();
  return pendingPolicy;
}

export { POLICY_NOT_SET };

if (typeof window !== 'undefined') {
  ensurePolicy().catch(() => {});
}
