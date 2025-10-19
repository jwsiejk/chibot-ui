const BREADCRUMB_URL = '/api/v1/flow/breadcrumbs';

function isEnabled() {
  try {
    return Boolean(globalThis?.__FLOW_BREADCRUMBS_ENABLED);
  } catch {
    return false;
  }
}

function getSessionId() {
  try {
    return globalThis?.__askchip_voice_session_id || null;
  } catch {
    return null;
  }
}

function getTurnId() {
  try {
    const turn = globalThis?.__askchip_turn_trace_id;
    if (typeof turn === 'string' && turn) return turn;
    if (Number.isFinite(turn)) return String(turn);
  } catch {}
  return null;
}

function sanitizeDetail(detail) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  try {
    return JSON.parse(JSON.stringify(detail));
  } catch {
    const safe = {};
    for (const [key, value] of Object.entries(detail)) {
      if (value === undefined) {
        continue;
      }
      if (value === null || typeof value === 'string' || typeof value === 'boolean') {
        safe[key] = value;
        continue;
      }
      if (typeof value === 'number') {
        safe[key] = Number.isFinite(value) ? value : null;
        continue;
      }
      if (Array.isArray(value)) {
        safe[key] = value
          .map((item) => {
            if (item === null || typeof item === 'string' || typeof item === 'boolean') {
              return item;
            }
            if (typeof item === 'number') {
              return Number.isFinite(item) ? item : null;
            }
            if (typeof item === 'object') {
              return sanitizeDetail(item);
            }
            return String(item);
          })
          .filter((item) => item !== undefined);
        continue;
      }
      if (typeof value === 'object') {
        safe[key] = sanitizeDetail(value);
        continue;
      }
      try {
        safe[key] = String(value);
      } catch {
        safe[key] = null;
      }
    }
    return safe;
  }
}

export function emitFlowBreadcrumb(name, detail = {}) {
  if (typeof name !== 'string' || !name) {
    return;
  }
  if (!isEnabled()) {
    return;
  }

  const envelope = {
    name,
    ts_ms: Date.now(),
    session_id: getSessionId(),
    turn_id: getTurnId(),
    detail: sanitizeDetail(detail),
  };

  try {
    const body = JSON.stringify(envelope);
    if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
      try {
        const blob = new Blob([body], { type: 'application/json' });
        if (navigator.sendBeacon(BREADCRUMB_URL, blob)) {
          return;
        }
      } catch {}
    }
    if (typeof fetch === 'function') {
      fetch(BREADCRUMB_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
        credentials: 'same-origin',
      }).catch(() => {});
    }
  } catch {}
}
