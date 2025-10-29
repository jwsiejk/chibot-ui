(() => {
  const HEARTBEAT_INTERVAL_MS = 20000;
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const SUBPROTOCOL = "chat.v2";
  const INFO_DEADLINE_MS = 20000;
  const TOKEN_EXPIRY_MS = 60 * 1000;
  const TOAST_STYLE_ID = "wsclient-toast-styles";
  const TOAST_STYLE_TEXT = "#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:\"Inter\",system-ui,-apple-system,\"Segoe UI\",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}";

  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading WSClient");
  }
  const WSClient = window.WSClient = window.WSClient || {};
  WSClient._ws = WSClient._ws || null;
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
  const getAudioPlayer = () => window.AudioPlayer;

  try {
  if (typeof AppState.websocket === "undefined" && typeof AppState.getState === "function") {
    Object.defineProperty(AppState, "websocket", {
      configurable: true, enumerable: false,
      get() { try { return AppState.getState().websocket || null; } catch { return null; } }
    });
  }
} catch {}

  let socket = null;
  let heartbeatTimerId = null;
  let expectInfoFrame = true;
  let infoWatchdogTimerId = null;
  let lastPingAt = null;
  let transportFactory = (url, protocols = SUBPROTOCOL) => new WebSocket(url, protocols);
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;
  let autoResumeAttemptToken = null;
  let toastRoot = null;
  let lastTokenValue = null;
  let lastTokenMintedAt = null;

  const MEDIA_RECORDER_MIME_TYPE = "audio/webm;codecs=opus";
  const DEFAULT_TIMESLICE_MS = 250;
  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";
  const CLIENT_BANNER_TYPE = "client.banner";
  const CLIENT_BANNER_MAX_HISTORY = 24;
  const CLIENT_BANNER_MAX_QUEUE = 24;
  const CLIENT_BANNER_EVENT_LABEL_MAX = 64;
  const CLIENT_BANNER_STRING_MAX = 240;

  let micStream = null;
  let micStreamPromise = null;
  let mediaRecorderInstance = null;
  let mediaRecorderHandlers = null;
  let activeInputReqId = null;
  let micOpenEmitted = false;
  let inputDescriptor = null;
  let inputVendor = null;
  let hudListening = false;
  let clientBannerQueue = [];

  // Initialize the client banner state only after related constants are defined.
  ensureClientBannerState();

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

  function ensureClientBannerState() {
    const state = AppState.getState();
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
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    while (clientBannerQueue.length) {
      const next = clientBannerQueue[0];
      try {
        sendJson(next);
        clientBannerQueue.shift();
      } catch (err) {
        console.warn("Failed to flush client banner", err);
        break;
      }
    }
  }

  function recordClientBannerEvent(label, meta) {
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
  }

  function getErrorMessage(err) {
    if (!err) {
      return undefined;
    }
    if (typeof err === "string") {
      return truncateBannerString(err);
    }
    if (err && typeof err.message === "string") {
      return truncateBannerString(err.message);
    }
    try {
      return truncateBannerString(String(err));
    } catch (inner) {
      return undefined;
    }
  }

  // Build a full WS URL and PRESERVE the query string as-is.
  function makeWsUrl(pathWithQuery) {
    if (typeof pathWithQuery !== "string" || !pathWithQuery) {
      return pathWithQuery;
    }
    const trimmed = pathWithQuery.trim();
    if (/^wss?:/i.test(trimmed)) {
      return trimmed;
    }
    try {
      const loc = (typeof window !== "undefined" && window.location)
        ? window.location
        : (typeof location !== "undefined" ? location : null);
      const scheme = loc && loc.protocol === "https:" ? "wss://" : "ws://";
      const parsed = new URL(trimmed, loc ? loc.origin : undefined);
      // IMPORTANT: keep the search/query intact
      return `${scheme}${parsed.host}${parsed.pathname}${parsed.search}`;
    } catch (err) {
      console.warn("Failed to construct WS URL", err);
      return trimmed;
    }
  }

  function getResumeState() {
    const state = AppState.getState();
    const resume = state && typeof state.resume === "object" ? state.resume : null;
    if (!resume) return null;
    const token = typeof resume.token === "string" ? resume.token : null;
    const ttlMs = Number.isFinite(resume.ttlMs) ? resume.ttlMs : null;
    const expiresAt = Number.isFinite(resume.expiresAt) ? resume.expiresAt : null;
    if (!token || !ttlMs || !expiresAt) return null;
    return { token, ttlMs, expiresAt };
  }

  function assignResume(token, ttlMs) {
    if (typeof AppState.setResume === "function") {
      AppState.setResume(token, ttlMs);
    }
    autoResumeAttemptToken = null;
  }

  function clearResumeState() {
    if (typeof AppState.clearResume === "function") {
      AppState.clearResume();
    }
    autoResumeAttemptToken = null;
  }

  function updateState(patch) {
    AppState.setState(patch);
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

  function trackTokenFromUrl(url) {
    if (typeof url !== "string" || !url) {
      return { token: null, mintedAt: null };
    }
    let token = null;
    try {
      const parsed = new URL(url, window.location.origin);
      token = parsed.searchParams.get("access_token");
    } catch (err) {
      return { token: null, mintedAt: null };
    }
    if (typeof token === "string" && token) {
      if (token !== lastTokenValue) {
        lastTokenValue = token;
        lastTokenMintedAt = Date.now();
      }
      return { token: lastTokenValue, mintedAt: lastTokenMintedAt };
    }
    return { token: null, mintedAt: null };
  }

  function maybeShowHandshakeToast(ws, closeCode) {
    if (!ws || ws.__intentionalClose === true || ws.__handshakeToastShown) {
      return;
    }
    if (ws.readyState !== WebSocket.CLOSED || closeCode !== 1006) {
      return;
    }
    const info = ws.__accessTokenInfo || { token: null, mintedAt: null };
    const mintedAt = info && typeof info.mintedAt === "number" && Number.isFinite(info.mintedAt)
      ? info.mintedAt
      : null;
    let message = null;
    if (!mintedAt) {
      message = "Couldn’t connect. Please login or complete your profile.";
    } else if (Date.now() - mintedAt > TOKEN_EXPIRY_MS) {
      message = "Session token expired. Click Start again.";
    } else {
      message = "Connection failed. Please try again.";
    }
    showConnectionToast(message);
    ws.__handshakeToastShown = true;
    recordClientBannerEvent("ws.handshake.toast", {
      message,
      minted_ms: mintedAt,
      close_code: closeCode,
    });
  }

  function computeUrl(resumeToken) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = `${protocol}//${window.location.host}/ws/v2/chat`;
    const params = new URLSearchParams();
    if (typeof resumeToken === "string" && resumeToken.trim()) {
      params.set("resume", resumeToken.trim());
    }
    const query = params.toString();
    return query ? `${base}?${query}` : base;
  }

  function clearInfoWatchdog() {
    if (infoWatchdogTimerId) {
      clearTimeout(infoWatchdogTimerId);
      infoWatchdogTimerId = null;
    }
  }

  function startInfoWatchdog() {
    clearInfoWatchdog();
    infoWatchdogTimerId = setTimeout(() => {
      infoWatchdogTimerId = null;
      console.warn("WS info frame not received within deadline");
      recordClientBannerEvent("ws.info.deadline", null);
    }, INFO_DEADLINE_MS);
  }

  function clearHeartbeat() {
    if (heartbeatTimerId) {
      clearInterval(heartbeatTimerId);
      heartbeatTimerId = null;
    }
    if (window.WSClient) {
      window.WSClient._hb = null;
    }
    updateState({ heartbeatTimerId: null, lastPingAt: null });
  }

  function clearRateLimitRetryTimer() {
    if (rateLimitRetryTimerId) {
      clearTimeout(rateLimitRetryTimerId);
      rateLimitRetryTimerId = null;
    }
  }

  function resetRateLimitRecovery() {
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    if (window.WSErrorUI && typeof window.WSErrorUI.clearRateLimitToast === "function") {
      try {
        window.WSErrorUI.clearRateLimitToast();
      } catch (err) {
        console.warn("Failed to clear rate limit toast", err);
      }
    }
  }

  function scheduleRateLimitRetry(delayMs, callbacks = {}) {
    const delay = Number(delayMs);
    if (!Number.isFinite(delay) || delay <= 0) return false;
    if (rateLimitRetryTimerId || rateLimitRetryCount >= 1) return false;
    rateLimitRetryCount += 1;
    recordClientBannerEvent("ws.retry.scheduled", { delay_ms: delay });
    rateLimitRetryTimerId = setTimeout(() => {
      rateLimitRetryTimerId = null;
      if (callbacks && typeof callbacks.onRetryStart === "function") {
        try {
          callbacks.onRetryStart();
        } catch (err) {
          console.warn("Auto-retry callback failed", err);
        }
      }
      const state = AppState.getState();
      const resumeState = state && typeof state.resume === "object" ? state.resume : null;
      let resumeToken = null;
      if (resumeState && typeof resumeState.token === "string" && Number.isFinite(resumeState.expiresAt) && Date.now() < resumeState.expiresAt) {
        resumeToken = resumeState.token;
      }
      try {
        recordClientBannerEvent("ws.retry.begin", { resume_token_present: Boolean(resumeToken) });
        open({ resumeToken, skipRateLimitCancel: true });
      } catch (err) {
        console.error("Auto-retry open failed", err);
        recordClientBannerEvent("ws.retry.failed", { message: getErrorMessage(err) });
      }
    }, delay);
    return true;
  }

  function sendBinary(payload, opts = {}) {
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (options.lane === "mic") {
      options.dropIfBusy = false;
    }
    const dropIfBusy = Boolean(options.dropIfBusy);
    const client = WSClient;
    const state = typeof AppState !== "undefined" && typeof AppState.getState === "function"
      ? AppState.getState()
      : null;
    const live = client && client._ws
      ? client._ws
      : (state && state.websocket ? state.websocket : null);
    if (dropIfBusy && live && live.readyState === WebSocket.OPEN && live.bufferedAmount > 512 * 1024) {
      return false;
    }
    if (client && typeof client.send === "function") {
      const result = client.send(payload, { binary: true });
      if (result && typeof result.then === "function") {
        return result;
      }
      return true;
    }
    try {
      if (live && live.readyState === WebSocket.OPEN) {
        live.send(payload);
        return true;
      }
    } catch (err) {
      console.error("WSClient sendBinary error", err);
    }
    return false;
  }

  function sendJson(frame) {
    try {
      send(frame);
    } catch (err) {
      console.error("WSClient sendJson error", err);
    }
  }

  function canSendHeartbeat() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    const client = WSClient;
    if (client && typeof client.isConnected === "function") {
      try {
        if (!client.isConnected()) {
          return false;
        }
      } catch (err) {
        console.warn("WSClient.isConnected check failed", err);
      }
    }
    return true;
  }

  function sendPing() {
    if (!canSendHeartbeat()) return;
    lastPingAt = Date.now();
    updateState({ lastPingAt });
    send({ type: "client.ping", ts: lastPingAt });
  }

  function startHeartbeat() {
    clearHeartbeat();
    const intervalId = setInterval(sendPing, HEARTBEAT_INTERVAL_MS);
    heartbeatTimerId = intervalId;
    if (window.WSClient) {
      window.WSClient._hb = intervalId;
    }
    sendPing();
    updateState({ heartbeatTimerId: intervalId });
  }

  function attemptAutoResume() {
    const resume = getResumeState();
    if (!resume) {
      return false;
    }
    if (Date.now() >= resume.expiresAt) {
      clearResumeState();
      updateState({ resumeError: "invalid" });
      recordClientBannerEvent("ws.auto_resume.expired", { expires_at_ms: resume.expiresAt });
      return false;
    }
    const state = AppState.getState();
    if (autoResumeAttemptToken === resume.token) {
      return false;
    }
    autoResumeAttemptToken = resume.token;
    updateState({ connectionState: "resuming", resumeError: null });
    recordClientBannerEvent("ws.auto_resume.start", { resume_token_present: true, expires_at_ms: resume.expiresAt });
    try {
      open({ resumeToken: resume.token, skipRateLimitCancel: true });
      return true;
    } catch (err) {
      console.error("Auto-resume open failed", err);
      autoResumeAttemptToken = null;
      updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
      recordClientBannerEvent("ws.auto_resume.failed", { message: getErrorMessage(err) });
      return false;
    }
  }

  function dispatchFrame(frame) {
    if (!frame || typeof frame.type !== "string") return;
    const type = frame.type;
    try {
      window.dispatchEvent(new CustomEvent(type, { detail: frame }));
      if (type.includes(".")) {
        const baseType = type.split(".")[0];
        window.dispatchEvent(new CustomEvent(baseType, { detail: frame }));
      }
    } catch (err) {
      console.warn("WSClient dispatch error", err);
    }
  }

  function emitHudState(state) {
    if (typeof window === "undefined" || !state) {
      return;
    }
    try {
      window.dispatchEvent(new CustomEvent(CLIENT_HUD_STATE_EVENT, {
        detail: {
          type: CLIENT_HUD_STATE_EVENT,
          meta: { state, source: "client" }
        }
      }));
    } catch (err) {
      console.warn("WSClient HUD event dispatch failed", err);
    }
  }

  function updateHudListening(active) {
    const normalized = Boolean(active);
    if (hudListening === normalized) {
      return;
    }
    hudListening = normalized;
    emitHudState(normalized ? "Listening" : "Idle");
  }

  function emitClientMicOpenIfNeeded() {
    if (micOpenEmitted) {
      return;
    }
    micOpenEmitted = true;
    if (typeof window !== "undefined") {
      const detail = {
        type: CLIENT_MIC_OPEN_EVENT,
        ts: Date.now(),
        vendor: inputVendor || null
      };
      try {
        window.dispatchEvent(new CustomEvent(CLIENT_MIC_OPEN_EVENT, { detail }));
      } catch (err) {
        console.warn("WSClient mic open event dispatch failed", err);
      }
      if (socket && socket.readyState === WebSocket.OPEN) {
        const payload = {
          type: "client.ready",
          mic: {
            state: "open",
            ts: detail.ts
          }
        };
        if (detail.vendor) {
          payload.mic.vendor = detail.vendor;
        }
        try {
          sendJson(payload);
        } catch (err) {
          console.warn("Failed to send client.ready mic frame", err);
        }
      }
    }
  }

  function stopMicStream() {
    if (!micStream) {
      return;
    }
    const tracks = typeof micStream.getTracks === "function" ? micStream.getTracks() : [];
    tracks.forEach((track) => {
      if (track && typeof track.stop === "function") {
        try {
          track.stop();
        } catch (err) {
          console.warn("Failed to stop mic track", err);
        }
      }
    });
    micStream = null;
    if (typeof window !== "undefined") {
      window.__micStream = null;
    }
  }

  async function ensureMicStream() {
    if (micStream) {
      const audioTracks = typeof micStream.getAudioTracks === "function" ? micStream.getAudioTracks() : [];
      const hasLiveTrack = audioTracks.some((track) => track && track.readyState === "live");
      if (hasLiveTrack) {
        return micStream;
      }
      stopMicStream();
    }
    if (micStreamPromise) {
      return micStreamPromise;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      console.error("Media capture is not supported in this browser");
      return null;
    }
    const constraints = {
      audio: {
        echoCancellation: true,
        noiseSuppression: true
      }
    };
    micStreamPromise = navigator.mediaDevices.getUserMedia(constraints)
      .then((stream) => {
        micStreamPromise = null;
        micStream = stream;
        if (typeof window !== "undefined") {
          window.__micStream = stream;
        }
        return stream;
      })
      .catch((err) => {
        micStreamPromise = null;
        console.error("getUserMedia for input capture failed", err);
        throw err;
      });
    return micStreamPromise;
  }

  function handleAsrReadyFrame(frame) {
    const descriptor = frame && frame.input;
    if (!descriptor || typeof descriptor !== "object") {
      inputDescriptor = null;
      inputVendor = frame && typeof frame.vendor === "string" && frame.vendor ? frame.vendor : null;
      micOpenEmitted = false;
      return;
    }
    inputDescriptor = {
      container: typeof descriptor.container === "string" ? descriptor.container.toLowerCase() : "",
      codec: typeof descriptor.codec === "string" ? descriptor.codec.toLowerCase() : "",
      rate_hz: Number(descriptor.rate_hz) || null,
      channels: Number(descriptor.channels) || null
    };
    inputVendor = frame && typeof frame.vendor === "string" && frame.vendor ? frame.vendor : null;
    micOpenEmitted = false;
  }

  function stopInputCapture(options = {}) {
    activeInputReqId = null;
    micOpenEmitted = false;
    const recorder = mediaRecorderInstance;
    const handlers = mediaRecorderHandlers;
    mediaRecorderInstance = null;
    mediaRecorderHandlers = null;
    if (typeof window !== "undefined") {
      window.__mediaRecorder = null;
    }
    if (recorder) {
      if (handlers) {
        if (handlers.data) {
          recorder.removeEventListener("dataavailable", handlers.data);
        }
        if (handlers.error) {
          recorder.removeEventListener("error", handlers.error);
        }
        if (handlers.stop) {
          recorder.removeEventListener("stop", handlers.stop);
        }
      }
      try {
        if (recorder.state !== "inactive") {
          recorder.stop();
        }
      } catch (err) {
        console.warn("Failed to stop MediaRecorder", err);
      }
    }
    if (!options || options.resetStream !== false) {
      micStreamPromise = null;
      stopMicStream();
    }
    updateHudListening(false);
  }

  async function startInputCapture(frame) {
    if (typeof MediaRecorder === "undefined") {
      console.error("MediaRecorder is not supported in this browser");
      return;
    }
    if (typeof MediaRecorder.isTypeSupported === "function" && !MediaRecorder.isTypeSupported(MEDIA_RECORDER_MIME_TYPE)) {
      console.error("MediaRecorder does not support", MEDIA_RECORDER_MIME_TYPE);
      return;
    }
    if (!inputDescriptor || inputDescriptor.container !== "webm" || inputDescriptor.codec !== "opus") {
      console.error("Unsupported or missing ASR descriptor", inputDescriptor);
      return;
    }
    const reqId = frame && typeof frame.req_id === "string" && frame.req_id ? frame.req_id : null;
    if (
      reqId &&
      activeInputReqId &&
      mediaRecorderInstance &&
      mediaRecorderInstance.state !== "inactive" &&
      reqId === activeInputReqId
    ) {
      console.log("Duplicate input.start for req_id", reqId);
      return;
    }
    if (mediaRecorderInstance) {
      stopInputCapture({ resetStream: false });
    }
    const capture = frame && frame.capture;
    const timesliceCandidate = capture && Number(capture.timeslice_ms);
    const timesliceMs = Number.isFinite(timesliceCandidate) && timesliceCandidate > 0
      ? timesliceCandidate
      : DEFAULT_TIMESLICE_MS;
    let stream;
    try {
      stream = await ensureMicStream();
    } catch (err) {
      console.error("Failed to acquire microphone stream", err);
      return;
    }
    if (!stream) {
      console.error("Microphone stream unavailable for input.start");
      return;
    }
    let recorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType: MEDIA_RECORDER_MIME_TYPE });
    } catch (err) {
      console.error("Failed to create MediaRecorder", err);
      return;
    }
    const dataHandler = async (event) => {
      if (!event || !event.data || !event.data.size) {
        return;
      }
      if (recorder !== mediaRecorderInstance) {
        return;
      }
      try {
        const buffer = await event.data.arrayBuffer();
        if (!buffer || !buffer.byteLength) {
          return;
        }
        const sent = sendBinary(buffer, { dropIfBusy: true });
        if (!sent) {
          console.warn("Dropped input audio chunk due to backpressure");
        }
      } catch (err) {
        console.error("Failed to process MediaRecorder data", err);
      }
    };
    const errorHandler = (event) => {
      console.error("MediaRecorder error", event);
      stopInputCapture({ resetStream: false });
    };
    const stopHandler = () => {
      if (recorder === mediaRecorderInstance) {
        mediaRecorderInstance = null;
        mediaRecorderHandlers = null;
        activeInputReqId = null;
        if (typeof window !== "undefined") {
          window.__mediaRecorder = null;
        }
        updateHudListening(false);
        micOpenEmitted = false;
        micStreamPromise = null;
        stopMicStream();
      }
    };
    recorder.addEventListener("dataavailable", dataHandler);
    recorder.addEventListener("error", errorHandler);
    recorder.addEventListener("stop", stopHandler);
    mediaRecorderInstance = recorder;
    mediaRecorderHandlers = { data: dataHandler, error: errorHandler, stop: stopHandler };
    if (typeof window !== "undefined") {
      window.__mediaRecorder = recorder;
    }
    activeInputReqId = reqId;
    updateHudListening(true);
    try {
      recorder.start(timesliceMs);
      emitClientMicOpenIfNeeded();
    } catch (err) {
      console.error("MediaRecorder start failed", err);
      stopInputCapture();
    }
  }

  function handleInputStartFrame(frame) {
    Promise.resolve()
      .then(() => startInputCapture(frame))
      .catch((err) => {
        console.error("input.start handler error", err);
      });
  }

  function handleInputStopFrame() {
    stopInputCapture({ reason: "input.stop" });
  }

  function sanitizeServerBannerFrame(frame) {
    const safe = { type: "server.banner" };
    if (!frame || typeof frame !== "object") {
      return safe;
    }

    const stringKeys = [
      "build_id",
      "host",
      "cwd",
      "python",
      "platform",
      "ws_path",
      "subprotocol_selected",
      "adapter_file",
      "engine_file",
      "asr_file"
    ];
    for (const key of stringKeys) {
      if (typeof frame[key] === "string" && frame[key]) {
        safe[key] = frame[key];
      }
    }

    if (Number.isFinite(frame.pid)) {
      safe.pid = frame.pid;
    }

    if (Array.isArray(frame.subprotocols_offered)) {
      const subs = frame.subprotocols_offered.filter((value) => typeof value === "string" && value);
      if (subs.length) {
        safe.subprotocols_offered = subs;
      }
    }

    return safe;
  }

  function handleServerBannerFrame(frame) {
    const sanitized = sanitizeServerBannerFrame(frame);
    updateState({ serverBanner: sanitized });
    console.log("WS server banner", sanitized);
    dispatchFrame(sanitized);
  }

  function sanitizePolicyFrame(frame) {
    const safe = { type: "policy.interaction" };
    if (!frame || typeof frame !== "object") {
      safe.policy = {};
      return safe;
    }
    Object.keys(frame).forEach((key) => {
      if (key === "policy") return;
      safe[key] = frame[key];
    });
    const policy = {};
    const source = frame.policy;
    if (source && typeof source === "object") {
      if (typeof source.mode === "string") {
        policy.mode = source.mode;
      }
      if (typeof source.allow_auto_vad === "boolean") {
        policy.allow_auto_vad = source.allow_auto_vad;
      }
      if (typeof source.barge_in_enabled === "boolean") {
        policy.barge_in_enabled = source.barge_in_enabled;
      }
      if (typeof source.ws_auth_mode === "string" && source.ws_auth_mode.trim()) {
        policy.ws_auth_mode = source.ws_auth_mode.trim();
      }
    }
    safe.policy = policy;
    return safe;
  }

  function handleInfoFrame(frame) {
    const meta = frame && frame.meta;
    if (!meta || typeof meta.sid !== "string") {
      console.error("Invalid info frame", frame);
      close("bad_info_frame");
      return;
    }
    expectInfoFrame = false;
    clearInfoWatchdog();
    resetRateLimitRecovery();
    const resumeToken = typeof meta.resume_token === "string" ? meta.resume_token : null;
    const resumeTtlMs = Number.isFinite(meta.resume_ttl_ms) ? meta.resume_ttl_ms : null;
    if (resumeToken && resumeTtlMs) {
      assignResume(resumeToken, resumeTtlMs);
    } else {
      clearResumeState();
    }
    const descriptor = meta.tts_audio || frame.audio || (frame.meta && frame.meta.audio);
    const audioPlayer = getAudioPlayer();
    if (descriptor && audioPlayer && typeof audioPlayer.setDescriptor === "function") {
      audioPlayer.setDescriptor(descriptor);
    }
    updateState({
      connectionState: "connected",
      sid: meta.sid,
      infoFrame: frame,
      resumeError: null
    });
    flushClientBannerQueue();
  }

  function handlePongFrame(frame) {
    const now = Date.now();
    let reference = null;
    if (frame && typeof frame.echo === "number") {
      reference = frame.echo;
    } else if (frame && typeof frame.t === "number") {
      reference = frame.t;
    } else if (typeof lastPingAt === "number") {
      reference = lastPingAt;
    }
    if (typeof reference === "number") {
      const latency = Math.max(0, now - reference);
      updateState({ latencyMs: latency });
    }
  }

  function handleChatHistoryFrame(frame) {
    const view = window.TranscriptView;
    const messages = Array.isArray(frame.messages) ? frame.messages : [];
    if (view && typeof view.handleChatMessage === "function" && messages.length) {
      for (const message of messages) {
        try {
          view.handleChatMessage(message);
        } catch (err) {
          console.warn("TranscriptView chat history handler error", err);
          break;
        }
      }
    }
  }

  function handleErrorFrame(frame) {
    console.error("WS error frame", frame);
    const errorMeta = {};
    if (frame && typeof frame.code === "string") {
      errorMeta.code = truncateBannerString(frame.code, 64);
    }
    if (frame && typeof frame.detail === "string" && frame.detail) {
      errorMeta.detail = truncateBannerString(frame.detail, 160);
    } else if (frame && typeof frame.message === "string" && frame.message) {
      errorMeta.detail = truncateBannerString(frame.message, 160);
    }
    if (Object.keys(errorMeta).length) {
      recordClientBannerEvent("ws.error.frame", errorMeta);
    } else {
      recordClientBannerEvent("ws.error.frame", null);
    }
    const isResumeInvalid = frame && frame.code === "resume_invalid";
    if (isResumeInvalid) {
      clearResumeState();
      updateState({ resumeError: "invalid" });
    }
    if (window.WSErrorUI && typeof window.WSErrorUI.handleFrame === "function") {
      try {
        window.WSErrorUI.handleFrame(frame, {
          scheduleRetry: (delayMs, callbacks) => scheduleRateLimitRetry(delayMs, callbacks)
        });
      } catch (err) {
        console.warn("Error handler threw", err);
      }
    }
    if (isResumeInvalid) {
      close("resume_invalid");
    }
  }

  function handleMessageFrame(frame) {
    if (frame.type === "keepalive") {
      // Some transports may emit a keepalive frame before the info frame has
      // been sent. Treat it as a no-op and continue waiting for the info
      // frame so we do not close the connection prematurely.
      return;
    }

    if (frame.type === "server.ping") {
      const response = { type: "client.pong", ts: Date.now() };
      if (typeof frame.ts === "number") {
        response.echo = frame.ts;
      }
      sendJson(response);
      return;
    }

    if (frame.type === "ping") {
      sendJson({ type: "pong", t: Date.now() });
      return;
    }

    if (frame.type === "policy.interaction") {
      const sanitized = sanitizePolicyFrame(frame);
      dispatchFrame(sanitized);
      return;
    }

    if (frame.type === "server.banner") {
      handleServerBannerFrame(frame);
      return;
    }

    if (expectInfoFrame) {
      if (frame.type === "chat.history") {
        handleChatHistoryFrame(frame);
      } else if (frame.type === "error") {
        handleErrorFrame(frame);
        return;
      } else if (frame.type !== "info") {
        console.error("Expected info frame first, received", frame.type);
        close("bad_info_sequence");
        return;
      }
      if (frame.type === "info") {
        handleInfoFrame(frame);
      }
    } else if (frame.type === "info") {
      handleInfoFrame(frame);
    } else if (frame.type === "server.pong") {
      handlePongFrame(frame);
    } else if (frame.type === "pong") {
      handlePongFrame(frame);
    } else if (frame.type === "error") {
      handleErrorFrame(frame);
    } else if (frame.type === "tts.start") {
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.handleTtsStart === "function") {
        audioPlayer.handleTtsStart(frame);
      }
    } else if (frame.type === "tts.end") {
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.handleTtsEnd === "function") {
        audioPlayer.handleTtsEnd(frame);
      }
    } else if (frame.type === "start_listening") {
      const recorder = window.AudioRecorder;
      if (recorder) {
        const handler = typeof recorder.startListening === "function"
          ? recorder.startListening
          : (typeof recorder.handleStartListening === "function" ? recorder.handleStartListening : null);
        if (handler) {
          try {
            handler.call(recorder, frame);
          } catch (err) {
            console.warn("AudioRecorder start_listening handler error", err);
          }
        }
      }
    } else if (frame.type === "input.start") {
      handleInputStartFrame(frame);
    } else if (frame.type === "input.stop") {
      handleInputStopFrame();
    } else if (frame.type === "asr.ready") {
      handleAsrReadyFrame(frame);
    } else if (frame.type === "asr.partial") {
      const view = window.TranscriptView;
      if (view && typeof view.handlePartial === "function") {
        try {
          view.handlePartial(frame);
        } catch (err) {
          console.warn("TranscriptView partial handler error", err);
        }
      }
    } else if (frame.type === "asr.final") {
      const view = window.TranscriptView;
      if (view && typeof view.handleFinal === "function") {
        try {
          view.handleFinal(frame);
        } catch (err) {
          console.warn("TranscriptView final handler error", err);
        }
      }
    } else if (frame.type === "asr.unavailable") {
      const reason = frame && typeof frame.reason === "string" ? frame.reason : "";
      const details = frame && typeof frame.details === "string"
        ? frame.details
        : (frame && typeof frame.detail === "string" ? frame.detail : "");
      console.warn("asr.unavailable", reason, details);
      try {
        const hud = window?.HUD || window?.DiagHUD || window?.DiagHud;
        hud?.setState?.("Chat");
      } catch (err) {
        console.warn("Failed to update HUD state after asr.unavailable", err);
      }
      try {
        const view = window.TranscriptView;
        view?.showSystemFromChip?.(
          "Sorry, having issues hearing you right now, but I can absolutely still assist via chat."
        );
      } catch (err) {
        console.warn("Failed to render Chip system message after asr.unavailable", err);
      }
      try {
        window?.Banner?.show?.(
          "Voice temporarily unavailable. You can continue via chat.",
          { level: "warning", ttlMs: 10000 }
        );
      } catch (err) {
        console.warn("Failed to show voice unavailable banner", err);
      }
    } else if (frame.type === "chat.message") {
      const view = window.TranscriptView;
      if (view && typeof view.handleChatMessage === "function") {
        try {
          view.handleChatMessage(frame);
        } catch (err) {
          console.warn("TranscriptView chat handler error", err);
        }
      }
    } else if (frame.type === "chat.history") {
      handleChatHistoryFrame(frame);
    }
    dispatchFrame(frame);
  }

  function parseFrame(event) {
    const { data } = event;
    if (typeof data === "string") {
      try {
        const frame = JSON.parse(data);
        if (frame && frame.type === "server.ping") {
          send({ type: "client.pong", ts: Date.now(), echo: frame.ts });
          return;
        }
        handleMessageFrame(frame);
      } catch (err) {
        console.error("Failed to parse WS frame", err, data);
      }
      return;
    }
    if (data instanceof Blob) {
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
        audioPlayer.enqueueChunk(data);
      }
      window.dispatchEvent(new CustomEvent("binary", { detail: data }));
      return;
    }
    if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
      const chunk = data instanceof ArrayBuffer
        ? data
        : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
        audioPlayer.enqueueChunk(chunk);
      }
      window.dispatchEvent(new CustomEvent("binary", { detail: chunk }));
      return;
    }
    console.warn("Unknown WS frame type", data);
  }

  function attachSocket(ws) {
    ws.__intentionalClose = false;
    const handlers = {
      open: () => {
  // Write live socket and mark connected so UI gates on ws.open can run
  try {
    if (typeof AppState.setState === "function") {
      AppState.setState({ websocket: ws, connectionState: "connected" });
    } else {
      updateState({ websocket: ws, connectionState: "connected" });
    }
  } catch {
    updateState({ websocket: ws, connectionState: "connected" });
  }
        try {
          WSClient._ws = ws;
          WSClient._connected = true;
        } catch {}
        try {
          if (!Array.isArray(WSClient._queue)) {
            WSClient._queue = [];
          }
          if (WSClient._queue.length) {
            for (const { data, isBinary } of WSClient._queue) {
              WSClient.send(data, { binary: isBinary });
            }
            WSClient._queue.length = 0;
          }
        } catch {}
        try {
          window.dispatchEvent(new CustomEvent("ws.open", { detail: { websocket: ws } }));
        } catch {}
        try {
          if (!WSClient._linkedProofLogged) {
            console.info("evt=ws_linked AppState", !!AppState?.websocket, "WSClient", !!WSClient._ws);
            WSClient._linkedProofLogged = true;
          }
        } catch {}
        startHeartbeat();
      },
      message: parseFrame,
      error: (event) => {
        console.error("WebSocket error", event);
        window.dispatchEvent(new CustomEvent("ws.error", { detail: event }));
      },
      close: (event) => {
        const expected = ws.__intentionalClose === true;
        try {
          WSClient._connected = false;
          WSClient._ws = null;
          WSClient._linkedProofLogged = false;
        } catch {}
        if (socket === ws) {
          socket = null;
          expectInfoFrame = true;
          clearInfoWatchdog();
        }
        clearHeartbeat();
        updateState({ websocket: null });
        let resumed = false;
        if (!expected) {
          resumed = attemptAutoResume();
        }
        if (!resumed) {
          updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
        }
        window.dispatchEvent(new CustomEvent("ws.close", { detail: event }));
      }
    };
    ws.addEventListener("open", handlers.open);
    ws.addEventListener("message", handlers.message);
    ws.addEventListener("error", handlers.error);
    ws.addEventListener("close", handlers.close);
    ws.__handlers = handlers;
  }

  function detachSocket(ws) {
    const handlers = ws && ws.__handlers;
    if (!handlers) return;
    ws.removeEventListener("open", handlers.open);
    ws.removeEventListener("message", handlers.message);
    ws.removeEventListener("error", handlers.error);
    ws.removeEventListener("close", handlers.close);
    delete ws.__handlers;
    delete ws.__intentionalClose;
  }

  function cleanupSocket(ws, reason = DEFAULT_CLOSE_REASON) {
    if (!ws) return;
    detachSocket(ws);
    ws.__intentionalClose = true;
    recordClientBannerEvent("ws.cleanup", { reason: truncateBannerString(reason || "", 80) });
    try {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, reason);
      }
    } catch (err) {
      console.warn("WSClient cleanup close error", err);
    }
    if (socket === ws) {
      socket = null;
      expectInfoFrame = true;
      clearInfoWatchdog();
      stopInputCapture();
      inputDescriptor = null;
      inputVendor = null;
    }
    const client = WSClient;
    if (client && typeof client === "object") {
      client._ws = null;
      client._connected = false;
    }
  }

  // OPEN: Use the EXACT URL provided (must include ?access_token=... unless resuming)
  function open(options = {}, protocolsOverride) {
    let resumeTokenValue = null;
    let skipRateLimitCancel = false;
    let urlOverride = null;
    let protocols = protocolsOverride;

    if (typeof options === "string") {
      urlOverride = options;
    } else if (options && typeof options === "object") {
      const { resumeToken = null, skipRateLimitCancel: skip = false } = options;
      const candidate = typeof resumeToken === "string" && resumeToken.trim() ? resumeToken.trim() : null;
      if (candidate) {
        resumeTokenValue = candidate;
      }
      skipRateLimitCancel = Boolean(skip);
      if (!urlOverride && typeof options.url === "string" && options.url.trim()) {
        urlOverride = options.url.trim();
      }
      if (protocols === undefined && options.protocols !== undefined) {
        protocols = options.protocols;
      }
    }

    if (socket) {
      cleanupSocket(socket, "superseded");
    }
    expectInfoFrame = true;
    startInfoWatchdog();
    if (!skipRateLimitCancel && window.WSErrorUI && typeof window.WSErrorUI.cancelRateLimitCountdown === "function") {
      try {
        window.WSErrorUI.cancelRateLimitCountdown("manual");
      } catch (err) {
        console.warn("Failed to cancel rate limit countdown", err);
      }
    }
    if (!skipRateLimitCancel) {
      clearRateLimitRetryTimer();
      rateLimitRetryCount = 0;
    }

    const originalUrl = urlOverride || computeUrl(resumeTokenValue);
    const wsUrl = typeof originalUrl === "string" ? makeWsUrl(originalUrl) : originalUrl;
    // Require a token unless we are resuming with a server-issued resume token
    const isResuming = Boolean(resumeTokenValue);
    const hasAccessToken = (typeof wsUrl === "string") && /[?&]access_token=/.test(wsUrl);
    const bannerUrlMeta = typeof wsUrl === "string" ? sanitizeUrlForBanner(wsUrl) : undefined;
    if (!isResuming && !hasAccessToken) {
      console.error("ws_open_missing_token_query", { originalUrl, finalUrl: wsUrl });
      showConnectionToast("Session token missing. Please click Start again.");
      updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
      recordClientBannerEvent("ws.open.blocked", {
        reason: "missing_access_token",
        url: bannerUrlMeta,
      });
      return;
    }

    const wsProtocols = protocols !== undefined ? protocols : SUBPROTOCOL;
    const bannerProtocols = Array.isArray(wsProtocols)
      ? wsProtocols
        .filter((proto) => typeof proto === "string" && proto)
        .map((proto) => truncateBannerString(proto, 48))
        .slice(0, 4)
      : (typeof wsProtocols === "string" && wsProtocols ? truncateBannerString(wsProtocols, 48) : undefined);
    recordClientBannerEvent("ws.open.request", {
      resume: isResuming,
      skip_rate_limit_cancel: Boolean(skipRateLimitCancel),
      has_access_token: hasAccessToken,
      url: bannerUrlMeta,
      protocols: bannerProtocols,
    });
    console.log("evt=ws_client_open_params", { url: wsUrl, protocols: wsProtocols });
    const logPayload = Array.isArray(wsProtocols)
      ? { url: wsUrl, protocols: wsProtocols }
      : { url: wsUrl, subprotocol: wsProtocols };
    console.log("WS opening", logPayload);

    const tokenInfo = trackTokenFromUrl(wsUrl);
    const ws = transportFactory(wsUrl, wsProtocols);
    ws.__accessTokenInfo = tokenInfo;
    ws.__handshakeToastShown = false;
    try {
      ws.binaryType = "arraybuffer";
    } catch (err) {
      console.warn("Failed to set WebSocket binaryType", err);
    }

    ws.onopen = () => {
      // Lightweight breadcrumb; keep as console.log
      console.log("WebSocket open", { url: wsUrl, protocol: ws.protocol || wsProtocols });
      recordClientBannerEvent("ws.socket.open", {
        protocol: truncateBannerString(ws.protocol || (typeof wsProtocols === "string" ? wsProtocols : ""), 48),
      });
      flushClientBannerQueue();
    };

    ws.onerror = (e) => {
      console.error("WebSocket error", e, { readyState: ws.readyState });
      maybeShowHandshakeToast(ws, null);
      recordClientBannerEvent("ws.socket.error", {
        ready_state: ws.readyState,
      });
    };

    ws.onclose = (e) => {
      console.error("WebSocket closed", {
        code: e.code,
        reason: e.reason,
        wasClean: e.wasClean,
        readyState: ws.readyState,
      });
      maybeShowHandshakeToast(ws, e && typeof e.code === "number" ? e.code : null);
      recordClientBannerEvent("ws.socket.close", {
        code: typeof e.code === "number" ? e.code : undefined,
        reason: truncateBannerString(e.reason || "", 160),
        was_clean: Boolean(e.wasClean),
        ready_state: ws.readyState,
      });
    };

    socket = ws;
    updateState({
      connectionState: resumeTokenValue ? "resuming" : "connecting",
      websocket: ws,
      latencyMs: null,
      lastPingAt: null,
      resumeError: null,
      infoFrame: null,
      serverBanner: null
    });
    attachSocket(ws);
    return ws;
  }

  function close(reason = DEFAULT_CLOSE_REASON) {
    recordClientBannerEvent("ws.close.request", { reason: truncateBannerString(reason || "", 80) });
    if (!socket) {
      updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
      return;
    }
    const ws = socket;
    cleanupSocket(ws, reason);
    clearHeartbeat();
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    if (window.WSErrorUI && typeof window.WSErrorUI.cancelRateLimitCountdown === "function") {
      try {
        window.WSErrorUI.cancelRateLimitCountdown(reason);
      } catch (err) {
        console.warn("Failed to cancel countdown on close", err);
      }
    }
    autoResumeAttemptToken = null;
    updateState({ connectionState: "disconnected", websocket: null, infoFrame: null, serverBanner: null });
  }

  function send(payload, { binary = false } = {}) {
    if (!Array.isArray(this._queue)) {
      this._queue = [];
    }
    let stateSocket = null;
    if (typeof AppState !== "undefined" && AppState) {
      stateSocket = AppState.websocket || null;
      if (!stateSocket && typeof AppState.getState === "function") {
        try {
          const snapshot = AppState.getState();
          stateSocket = snapshot && snapshot.websocket ? snapshot.websocket : null;
        } catch {}
      }
    }
    const live = this._ws || stateSocket;
    if (!live || live.readyState !== WebSocket.OPEN) {
      this._queue.push({ data: payload, isBinary: !!binary });
      console.warn("WSClient.send queued (socket not open)");
      return;
    }
    this._ws = live;
    this._connected = true;
    try { live.binaryType = "arraybuffer"; } catch {}
    if (binary) {
      if (payload instanceof Blob) {
        return payload.arrayBuffer().then((buf) => {
          try {
            live.send(buf);
          } catch (err) {
            console.error("WSClient binary send error", err);
          }
        });
      }
      if (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
        const buffer = payload instanceof ArrayBuffer
          ? payload
          : payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
        try {
          live.send(buffer);
        } catch (err) {
          console.error("WSClient binary send error", err);
        }
        return;
      }
    }
    const text = typeof payload === "string" ? payload : JSON.stringify(payload);
    try {
      live.send(text);
    } catch (err) {
      console.error("WSClient send error", err);
    }
  }

  function getBufferedAmount() {
    if (!socket) return 0;
    return socket.bufferedAmount || 0;
  }

  const debug = {
    simulateIncomingFrame(frame) {
      handleMessageFrame(frame);
    },
    recordPing(ts) {
      lastPingAt = ts;
      updateState({ lastPingAt: ts });
    },
    setTransportFactory(factory) {
      transportFactory = typeof factory === "function" ? factory : transportFactory;
    },
    resetTransportFactory() {
      transportFactory = (url, protocols = SUBPROTOCOL) => new WebSocket(url, protocols);
    }
  };

  WSClient.open = open;
  WSClient.close = close;
  WSClient.send = send;
  WSClient.sendBinary = sendBinary;
  WSClient.getBufferedAmount = getBufferedAmount;
  Object.defineProperty(WSClient, "socket", {
    configurable: true,
    enumerable: true,
    get() {
      return socket;
    }
  });
  WSClient.isConnected = function isConnected() {
    return !!socket && socket.readyState === WebSocket.OPEN && AppState.getState().connectionState === "connected";
  };
  WSClient.__debug = debug;
  window.WSClient = WSClient;
  WSClient._ws = WSClient._ws || null;
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
  if (typeof WSClient._linkedProofLogged !== "boolean") {
    WSClient._linkedProofLogged = false;
  }
})();
