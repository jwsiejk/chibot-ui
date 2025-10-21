const BREADCRUMB_URL = '/api/v1/flow/breadcrumbs';

try {
  console.log('client_source_components: enabled');
} catch {}

const AUDIO_PREFIXES = ['client_audio', 'audio_', 'playback_', 'play_'];
const AUDIO_EVENT_NAMES = new Set(['playback_start', 'playback_end', 'playing', 'ended']);

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function inferClientSource(name) {
  if (!isNonEmptyString(name)) {
    return 'client_ui';
  }
  const lowered = name.toLowerCase();
  if (lowered.startsWith('client_vad')) {
    return 'client_vad';
  }
  if (AUDIO_EVENT_NAMES.has(lowered)) {
    return 'client_audio';
  }
  if (AUDIO_PREFIXES.some((prefix) => lowered.startsWith(prefix))) {
    return 'client_audio';
  }
  return 'client_ui';
}

function enrichDetail(name, detail) {
  let payload = {};
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    payload = { ...detail };
  }

  if (!isNonEmptyString(payload.src)) {
    payload.src = inferClientSource(name);
  } else {
    payload.src = payload.src.trim();
  }

  if (!isNonEmptyString(payload.component)) {
    payload.component = payload.src;
  } else {
    payload.component = payload.component.trim();
  }

  return payload;
}

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

  const enrichedDetail = enrichDetail(name, detail);

  const envelope = {
    name,
    ts_ms: Date.now(),
    session_id: getSessionId(),
    turn_id: getTurnId(),
    detail: sanitizeDetail(enrichedDetail),
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
