const DEFAULT_ENABLED = true;

export function isAdvancedLoggingEnabled() {
  try {
    const cfg = typeof window !== 'undefined' ? window.__askchip_config : undefined;
    if (cfg && typeof cfg === 'object') {
      const logging = cfg.logging;
      if (logging && typeof logging === 'object' && 'enabled' in logging) {
        return !!logging.enabled;
      }
    }
  } catch {}
  return DEFAULT_ENABLED;
}

export function logIfEnabled(fn) {
  if (!isAdvancedLoggingEnabled()) return;
  try {
    if (typeof fn === 'function') {
      fn();
    }
  } catch {}
}

export const __TEST_ONLY__ = {
  isAdvancedLoggingEnabled,
  logIfEnabled,
};
