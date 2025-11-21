// Thin telemetry helpers used by ws_client.js

const TOAST_STYLE_ID = "wsclient-toast-styles";
const TOAST_STYLE_TEXT = "#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:\"Inter\",system-ui,-apple-system,\"Segoe UI\",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}";

const CLIENT_BANNER_TYPE = "client.banner";
const CLIENT_BANNER_MAX_HISTORY = 24;
const CLIENT_BANNER_MAX_QUEUE = 24;
const CLIENT_BANNER_EVENT_LABEL_MAX = 64;
const CLIENT_BANNER_STRING_MAX = 240;

export const MIC_OUTCOME = {
  PERM_GRANTED: 'perm_granted',
  ARMED: 'armed',
  STREAMING: 'streaming',
  STREAMING_HEARTBEAT: 'streaming_heartbeat',
  STOPPED: 'stopped',
  ERROR_DENIED: 'error_denied',
  ERROR_NO_DEVICE: 'error_no_device',
  ERROR_GUM: 'error_getuser_media',
  ERROR_SILENT: 'error_silent_stream',
  ERROR_WS_SEND: 'error_ws_send',
  ERROR_STATE_GUARD: 'error_state_guard',
  ERROR_SENDER_INIT: 'error_sender_init',
  ERROR_UNKNOWN: 'error_unknown',
};

let __micAttempts = 0;
let __micChunks = 0;
let __micBytes = 0;
let __micPermissionGranted = false;
let __micRecordingStartAt = null;
let __micFirstChunkBreadcrumbSent = false;
let __turnTraceId = null;

let clientBannerQueue = [];
let toastRoot = null;
let __hubLoggingInFlight = false;

function getTelemetrySocket() {
  if (typeof window !== "undefined") {
    if (window.__wsTelemetrySocket && typeof window.__wsTelemetrySocket.readyState === "number") {
      return window.__wsTelemetrySocket;
    }
    if (window.ws && typeof window.ws.readyState === "number") {
      return window.ws;
    }
  }
  return null;
}

function getTelemetrySendJson() {
  if (typeof window !== "undefined") {
    if (typeof window.__wsTelemetrySendJson === "function") {
      return window.__wsTelemetrySendJson;
    }
    if (window.WSClient && typeof window.WSClient.sendJSON === "function") {
      try {
        return window.WSClient.sendJSON.bind(window.WSClient);
      } catch (err) {
        return window.WSClient.sendJSON;
      }
    }
  }
  return null;
}

function getGateSnapshot() {
  let snapshot = null;
  try {
    snapshot = typeof AppState?.getState === "function" ? AppState.getState() : null;
  } catch {}
  const asrValue = snapshot && typeof snapshot.asrReady === "boolean"
    ? snapshot.asrReady
    : Boolean(AppState?.asrReady);
  const ttsValue = snapshot && typeof snapshot.ttsActive === "boolean"
    ? snapshot.ttsActive
    : Boolean(AppState?.ttsActive);
  let micPermValue = __micPermissionGranted;
  if (snapshot && typeof snapshot.micPermissionGranted === "boolean") {
    micPermValue = snapshot.micPermissionGranted;
  } else if (typeof AppState?.micPermissionGranted === "boolean") {
    micPermValue = AppState.micPermissionGranted;
  }
  return {
    asrReady: Boolean(asrValue),
    micPerm: Boolean(micPermValue),
    ttsActive: Boolean(ttsValue),
  };
}

export function emitMicBreadcrumb(detail = {}) {
  try {
    const payload = { ...detail };
    payload.gates = getGateSnapshot();
    hubLog('client.mic', payload);
  } catch (err) {
    try {
      console.warn("Mic breadcrumb log failed", err);
    } catch {}
  }
}

export function logMic(detail = {}) {
  try {
    const holdFlags = {
      ttsActive: !!AppState?.ttsActive,
      systemHold: !!AppState?.systemHold,
      userMuted: !!AppState?.userMuted,
    };
    const base = {
      trace_id: __turnTraceId || null,
      attempts: __micAttempts,
      chunks: __micChunks,
      bytes: __micBytes,
      phase: AppState?.ttsActive ? 'tts_active' : 'post_tts',
      hold_flags: holdFlags,
    };
    const outcome = typeof detail?.outcome === "string" ? detail.outcome : null;
    const permLabel = typeof detail?.perm === "string" ? detail.perm : null;
    if (permLabel !== null) {
      const granted = permLabel === "granted";
      __micPermissionGranted = granted;
      try { AppState.micPermissionGranted = granted; } catch {}
    } else if (outcome === MIC_OUTCOME.ERROR_DENIED) {
      __micPermissionGranted = false;
      try { AppState.micPermissionGranted = false; } catch {}
    }
    if (outcome === MIC_OUTCOME.PERM_GRANTED || permLabel === "granted") {
      emitMicBreadcrumb({ event: "armed" });
    }
    if (outcome === MIC_OUTCOME.STREAMING && !__micFirstChunkBreadcrumbSent) {
      __micFirstChunkBreadcrumbSent = true;
      let msSinceStart = 0;
      if (typeof __micRecordingStartAt === "number") {
        msSinceStart = Math.max(0, Math.round(Date.now() - __micRecordingStartAt));
      } else if (Number.isFinite(Number(detail?.first_chunk_ms))) {
        const fallback = Number(detail.first_chunk_ms);
        msSinceStart = Math.max(0, Math.round(fallback));
      }
      const bytesRaw = Number.isFinite(__micBytes) ? __micBytes : 0;
      const bytesSent = bytesRaw >= 0 ? bytesRaw : 0;
      emitMicBreadcrumb({
        event: "first_chunk_sent",
        bytes: bytesSent,
        ms_since_recording_start: msSinceStart,
      });
    }
    if (outcome === MIC_OUTCOME.STOPPED) {
      let totalMs = 0;
      if (typeof __micRecordingStartAt === "number") {
        totalMs = Math.max(0, Math.round(Date.now() - __micRecordingStartAt));
      }
      const reason = typeof detail?.reason === "string" && detail.reason ? detail.reason : null;
      emitMicBreadcrumb({
        event: "stopped",
        reason,
        ms_total_recording: totalMs,
      });
      __micRecordingStartAt = null;
      __micFirstChunkBreadcrumbSent = false;
    }
    hubLog('client.mic', { ...base, ...detail });
  } catch {}
}

export function emitClientLog(label, detail = {}) {
  if (typeof label !== "string" || !label) {
    return;
  }
  const payload = detail && typeof detail === "object" ? { ...detail } : {};
  try {
    hubLog(label, payload);
  } catch {}
  try {
    console.log(label, payload);
  } catch {}
}

export function logStage(label, detail = {}) {
  emitClientLog(label, { trace_id: __turnTraceId || null, ...detail });
}

export function normalizeErrorDetail(detail) {
  if (detail === null || detail === undefined) {
    return null;
  }
  if (typeof detail === "string") {
    return truncateBannerString(detail, 240);
  }
  if (typeof detail === "number" || typeof detail === "boolean") {
    return truncateBannerString(String(detail), 240);
  }
  try {
    const serialized = JSON.stringify(detail);
    return truncateBannerString(serialized, 240);
  } catch (err) {
    try {
      return truncateBannerString(String(detail), 240);
    } catch {
      return null;
    }
  }
}

export function recordLastError(code, detail) {
  const normalizedCode = Number.isFinite(code) ? code : null;
  const normalizedDetail = normalizeErrorDetail(detail);
  setAppStateValue("lastErrorCode", normalizedCode);
  setAppStateValue("lastErrorDetail", normalizedDetail);
}

export function recordClientBannerEvent(label, meta) {
  if (typeof label !== "string" || !label) {
    return;
  }
  const baseState = ensureClientBannerState();
  const info = collectClientBannerInfo();
  const events = Array.isArray(baseState.events) ? baseState.events.slice() : [];
  const entry = {
    label: truncateBannerString(label, CLIENT_BANNER_EVENT_LABEL_MAX),
    ts_ms: Date.now(),
  };
  const sanitizedMeta = sanitizeBannerValue(meta);
  if (sanitizedMeta && typeof sanitizedMeta === "object" && Object.keys(sanitizedMeta).length) {
    entry.meta = sanitizedMeta;
  }
  events.push(entry);
  if (events.length > CLIENT_BANNER_MAX_HISTORY) {
    events.splice(0, events.length - CLIENT_BANNER_MAX_HISTORY);
  }
  updateClientBannerState(info, events);
  queueClientBannerPayload({
    type: CLIENT_BANNER_TYPE,
    info,
    event: entry,
  });
  try {
    const normalizedMeta = sanitizedMeta && typeof sanitizedMeta === "object"
      ? sanitizedMeta
      : undefined;
    logStage("client.banner", { label: entry.label, meta: normalizedMeta });
  } catch {}
}

function ensureClientBannerState() {
  const state = typeof AppState?.getState === "function" ? AppState.getState() : null;
  const existing = state && state.clientBanner && typeof state.clientBanner === "object" ? state.clientBanner : null;
  if (existing && existing.info) {
    return {
      info: existing.info,
      events: Array.isArray(existing.events) ? existing.events : [],
    };
  }
  const info = collectClientBannerInfo();
  const snapshot = { info, events: [] };
  updateState({ clientBanner: snapshot });
  return snapshot;
}

function updateClientBannerState(info, events) {
  const state = {
    info,
    events,
  };
  updateState({ clientBanner: state });
  return state;
}

function queueClientBannerPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }
  clientBannerQueue = clientBannerQueue.concat([payload]);
  if (clientBannerQueue.length > CLIENT_BANNER_MAX_QUEUE) {
    clientBannerQueue = clientBannerQueue.slice(clientBannerQueue.length - CLIENT_BANNER_MAX_QUEUE);
  }
  flushClientBannerQueue();
}

function flushClientBannerQueue() {
  const socket = getTelemetrySocket();
  if (!socket) {
    return;
  }
  const readyState = socket.readyState;
  const openState = typeof WebSocket !== "undefined" ? WebSocket.OPEN : 1;
  if (readyState !== openState) {
    return;
  }
  const sendJson = getTelemetrySendJson();
  if (typeof sendJson !== "function") {
    return;
  }
  while (clientBannerQueue.length) {
    const next = clientBannerQueue[0];
    try {
      sendJson(next);
      clientBannerQueue.shift();
    } catch (err) {
      try {
        console.warn("Failed to flush client banner", err);
      } catch {}
      break;
    }
  }
}

function truncateBannerString(value, max) {
  const limit = typeof max === "number" ? max : CLIENT_BANNER_STRING_MAX;
  if (typeof value !== "string") {
    return undefined;
  }
  if (!limit || limit <= 0) {
    return "";
  }
  if (value.length <= limit) {
    return value;
  }
  return `${value.slice(0, limit - 1)}…`;
}

function sanitizeBannerNumber(value) {
  if (typeof value !== "number") {
    return undefined;
  }
  if (!Number.isFinite(value)) {
    return undefined;
  }
  return value;
}

function sanitizeBannerArray(array, depth) {
  if (!Array.isArray(array)) {
    return undefined;
  }
  const limit = 8;
  const result = [];
  for (let i = 0; i < array.length && result.length < limit; i += 1) {
    const sanitized = sanitizeBannerValue(array[i], depth + 1);
    if (sanitized !== undefined) {
      result.push(sanitized);
    }
  }
  return result;
}

function sanitizeBannerObject(obj, depth) {
  if (!obj || typeof obj !== "object") {
    return undefined;
  }
  const result = {};
  const keys = Object.keys(obj);
  const limit = 16;
  for (let i = 0; i < keys.length && Object.keys(result).length < limit; i += 1) {
    const rawKey = keys[i];
    if (typeof rawKey !== "string" || !rawKey) {
      continue;
    }
    const key = truncateBannerString(rawKey, 48);
    const sanitized = sanitizeBannerValue(obj[rawKey], depth + 1);
    if (sanitized !== undefined) {
      result[key] = sanitized;
    }
  }
  return result;
}

function sanitizeBannerValue(value, depth = 0) {
  if (value === null) {
    return null;
  }
  if (typeof value === "string") {
    return truncateBannerString(value);
  }
  if (typeof value === "number") {
    return sanitizeBannerNumber(value);
  }
  if (typeof value === "boolean") {
    return value;
  }
  if (depth >= 2) {
    return undefined;
  }
  if (Array.isArray(value)) {
    return sanitizeBannerArray(value, depth);
  }
  if (typeof value === "object") {
    return sanitizeBannerObject(value, depth);
  }
  return undefined;
}

function sanitizeUrlForBanner(url) {
  if (typeof url !== "string" || !url) {
    return undefined;
  }
  try {
    const parsed = new URL(url, window.location && window.location.origin ? window.location.origin : undefined);
    const info = {
      origin: truncateBannerString(parsed.origin, 120),
      pathname: truncateBannerString(parsed.pathname, 160),
    };
    if (parsed.hash) {
      info.hash = truncateBannerString(parsed.hash, 80);
    }
    if (parsed.search) {
      const params = new URLSearchParams(parsed.search);
      const keys = [];
      const iterator = params.keys();
      for (let i = 0; i < 12; i += 1) {
        const { value, done } = iterator.next();
        if (done) {
          break;
        }
        if (typeof value === "string" && value) {
          keys.push(truncateBannerString(value, 64));
        }
      }
      if (keys.length) {
        info.query_keys = keys;
      }
    }
    return info;
  } catch (err) {
    return { raw: truncateBannerString(url) };
  }
}

function collectClientBannerInfo() {
  if (typeof window === "undefined") {
    return {};
  }
  const info = {};
  const nav = typeof navigator !== "undefined" ? navigator : null;
  if (nav) {
    if (typeof nav.userAgent === "string" && nav.userAgent) {
      info.user_agent = truncateBannerString(nav.userAgent);
    }
    if (typeof nav.platform === "string" && nav.platform) {
      info.platform = truncateBannerString(nav.platform, 120);
    }
    if (typeof nav.vendor === "string" && nav.vendor) {
      info.vendor = truncateBannerString(nav.vendor, 120);
    }
    if (typeof nav.product === "string" && nav.product) {
      info.product = truncateBannerString(nav.product, 120);
    }
    if (typeof nav.language === "string" && nav.language) {
      info.language = truncateBannerString(nav.language, 32);
    }
    if (Array.isArray(nav.languages) && nav.languages.length) {
      info.languages = nav.languages
        .filter((lang) => typeof lang === "string" && lang)
        .slice(0, 8)
        .map((lang) => truncateBannerString(lang, 32));
    }
    if (typeof nav.hardwareConcurrency === "number" && Number.isFinite(nav.hardwareConcurrency)) {
      info.hardware_concurrency = Math.max(0, Math.floor(nav.hardwareConcurrency));
    }
    if (typeof nav.deviceMemory === "number" && Number.isFinite(nav.deviceMemory)) {
      info.device_memory = Math.max(0, nav.deviceMemory);
    }
    if (typeof nav.maxTouchPoints === "number" && Number.isFinite(nav.maxTouchPoints)) {
      info.max_touch_points = Math.max(0, nav.maxTouchPoints);
    }
    if (typeof nav.cookieEnabled === "boolean") {
      info.cookies_enabled = nav.cookieEnabled;
    }
    if (typeof nav.onLine === "boolean") {
      info.online = nav.onLine;
    }
    const connection = nav.connection || nav.mozConnection || nav.webkitConnection;
    if (connection && typeof connection === "object") {
      const conn = {};
      if (typeof connection.effectiveType === "string" && connection.effectiveType) {
        conn.effective_type = truncateBannerString(connection.effectiveType, 32);
      }
      if (typeof connection.type === "string" && connection.type) {
        conn.type = truncateBannerString(connection.type, 32);
      }
      if (typeof connection.downlink === "number" && Number.isFinite(connection.downlink)) {
        conn.downlink = connection.downlink;
      }
      if (typeof connection.rtt === "number" && Number.isFinite(connection.rtt)) {
        conn.rtt = connection.rtt;
      }
      if (Object.keys(conn).length) {
        info.connection = conn;
      }
    }
  }
  const screenInfo = typeof window.screen !== "undefined" ? window.screen : null;
  if (screenInfo) {
    const screenPayload = {};
    if (Number.isFinite(screenInfo.width) && Number.isFinite(screenInfo.height)) {
      screenPayload.width = screenInfo.width;
      screenPayload.height = screenInfo.height;
    }
    if (Number.isFinite(screenInfo.availWidth) && Number.isFinite(screenInfo.availHeight)) {
      screenPayload.avail_width = screenInfo.availWidth;
      screenPayload.avail_height = screenInfo.availHeight;
    }
    if (Object.keys(screenPayload).length) {
      info.screen = screenPayload;
    }
  }
  if (Number.isFinite(window.innerWidth) && Number.isFinite(window.innerHeight)) {
    info.viewport = {
      width: window.innerWidth,
      height: window.innerHeight,
    };
  }
  if (typeof window.devicePixelRatio === "number" && Number.isFinite(window.devicePixelRatio)) {
    info.device_pixel_ratio = window.devicePixelRatio;
  }
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions();
    if (tz && typeof tz.timeZone === "string" && tz.timeZone) {
      info.timezone = truncateBannerString(tz.timeZone, 96);
    }
  } catch (err) {
    // ignore timezone errors
  }
  const offset = new Date().getTimezoneOffset();
  if (Number.isFinite(offset)) {
    info.tz_offset_minutes = offset;
  }
  if (typeof document !== "undefined") {
    if (typeof document.referrer === "string" && document.referrer) {
      info.referrer = truncateBannerString(document.referrer);
    }
    if (typeof document.visibilityState === "string" && document.visibilityState) {
      info.visibility_state = truncateBannerString(document.visibilityState, 32);
    }
  }
  if (typeof window.matchMedia === "function") {
    try {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
      if (prefersDark && typeof prefersDark.matches === "boolean") {
        info.prefers_dark = prefersDark.matches;
      }
    } catch (err) {
      // ignore matchMedia errors
    }
  }
  if (typeof window.location !== "undefined") {
    const locationInfo = sanitizeUrlForBanner(window.location.href);
    if (locationInfo) {
      info.location = locationInfo;
    }
  }
  return info;
}

function ensureToastRoot() {
  toastRoot = toastRoot && toastRoot.isConnected ? toastRoot : document.getElementById("toast-root");
  if (!toastRoot) {
    toastRoot = document.createElement("div");
    toastRoot.id = "toast-root";
    toastRoot.className = "toast-container";
    document.body.appendChild(toastRoot);
  }
  if (
    !document.getElementById("inline-toast-styles") &&
    !document.getElementById("ws-error-styles") &&
    !document.getElementById(TOAST_STYLE_ID)
  ) {
    const styleTag = document.createElement("style");
    styleTag.id = TOAST_STYLE_ID;
    styleTag.textContent = TOAST_STYLE_TEXT;
    document.head.appendChild(styleTag);
  }
  return toastRoot;
}

function showConnectionToast(message) {
  if (!message) return;
  const host = ensureToastRoot();
  if (!host) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.setAttribute("role", "alert");
  const body = document.createElement("div");
  body.className = "toast-body";
  body.textContent = message;
  toast.appendChild(body);
  host.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("toast-exit");
    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 220);
  }, 3600);
}

function hubLog(label, detail) {
  if (__hubLoggingInFlight) {
    return false;
  }
  const state = typeof window !== "undefined" ? window.AppState : null;
  const hub = state && state.hub;
  if (hub && typeof hub.log === "function") {
    __hubLoggingInFlight = true;
    try {
      hub.log(label, detail);
      return true;
    } catch (err) {
      try {
        console.warn("AppState.hub.log failed", err);
      } catch {}
    } finally {
      __hubLoggingInFlight = false;
    }
  }
  if (typeof window !== "undefined") {
    try {
      window.dispatchEvent(new CustomEvent("client.log", { detail: { label, detail } }));
      return true;
    } catch (err) {
      try {
        console.warn("client.log dispatch failed", err);
      } catch {}
    }
  }
  return false;
}

function updateState(patch) {
  try {
    if (typeof AppState?.setState === "function") {
      AppState.setState(patch);
    } else {
      Object.assign(AppState, patch);
    }
  } catch {}
}

function setAppStateValue(key, value) {
  if (typeof key !== "string" || !key) {
    return;
  }
  try {
    const state = typeof AppState?.getState === "function" ? AppState.getState() : null;
    const hasKey = state && Object.prototype.hasOwnProperty.call(state, key);
    const current = hasKey ? state[key] : AppState[key];
    if (Object.is(current, value)) {
      AppState[key] = value;
      return;
    }
    AppState[key] = value;
    updateState({ [key]: value });
  } catch {}
}

if (typeof window !== "undefined") {
  try {
    Object.defineProperty(window, "__micAttempts", {
      configurable: true,
      get() { return __micAttempts; },
      set(value) {
        if (Number.isFinite(value)) {
          __micAttempts = value;
        }
      },
    });
    Object.defineProperty(window, "__micChunks", {
      configurable: true,
      get() { return __micChunks; },
      set(value) {
        if (Number.isFinite(value)) {
          __micChunks = value;
        }
      },
    });
    Object.defineProperty(window, "__micBytes", {
      configurable: true,
      get() { return __micBytes; },
      set(value) {
        if (Number.isFinite(value)) {
          __micBytes = value;
        }
      },
    });
    Object.defineProperty(window, "__micRecordingStartAt", {
      configurable: true,
      get() { return __micRecordingStartAt; },
      set(value) {
        if (Number.isFinite(value) || value === null) {
          __micRecordingStartAt = value;
        }
      },
    });
    Object.defineProperty(window, "__turnTraceId", {
      configurable: true,
      get() { return __turnTraceId; },
      set(value) {
        if (typeof value === "string" || value === null) {
          __turnTraceId = value;
        }
      },
    });
    Object.defineProperty(window, "__micPermGranted", {
      configurable: true,
      get() { return __micPermissionGranted; },
      set(value) {
        __micPermissionGranted = !!value;
      },
    });
  } catch {}
}

