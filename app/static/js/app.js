// app.js - FINAL, CORRECTED VERSION (with Recursive Call Guard)

(() => {
  // Ensure single-store shape (idempotent)
  try {
    window.AppState = window.AppState || {};
    window.AppState.policy = window.AppState.policy || {};
    if (typeof window.AppState.turnActive !== 'boolean') {
      window.AppState.turnActive = false;
    }
  } catch {}

  function hubLog(label, detail) {
    try { window.AppState?.hub?.log?.(label, detail); } catch {}
  }

  // Build stamp (diagnostic only). Prints the ?v= build id if present on this script.
  try {
    const el =
      document.currentScript ||
      document.querySelector('script[src*="/static/js/app.js"]');
    const build = el ? new URL(el.src, location.href).searchParams.get('v') : null;
    if (build) {
      window.__BUILD_SHA__ = build;
      console.log('AskChip build:', build);
    } else {
      console.log('AskChip build: (no v param on app.js)');
    }
  } catch (_) {}

  // Mic capture is centralized in pcm_recorder.js and follows policy.capture.constraints.
  let __gumInFlight = false;
  async function getMicOnce(constraints) {
    if (__gumInFlight) {
      return null;
    }
    __gumInFlight = true;
    try {
      if (!navigator?.mediaDevices?.getUserMedia) {
        throw new Error('MediaDevices.getUserMedia unavailable');
      }
      return await navigator.mediaDevices.getUserMedia(constraints);
    } finally {
      __gumInFlight = false;
    }
  }
  if (typeof window !== "undefined") {
    try {
      window.getMicOnce = getMicOnce;
    } catch (_) {}
  }

  (function wrapDiag() {
    const ctr = (window.__WS_DIAG__ = window.__WS_DIAG__ || { types: {} });
    function maybeWrap() {
      if (!window.WSClient) return;
      const method = typeof WSClient.sendJSON === 'function' ? 'sendJSON' : (typeof WSClient.send === 'function' ? 'send' : null);
      if (!method) return;
      const flag = method === 'sendJSON' ? '__wrapped_sendJSON__' : '__wrapped_send__';
      if (WSClient[flag]) return;
      const orig = WSClient[method].bind(WSClient);
      WSClient[method] = function (frame) {
        try {
          const t = frame && frame.type;
          if (t) ctr.types[t] = (ctr.types[t] || 0) + 1;
        } catch {}
        return orig(frame);
      };
      WSClient[flag] = true;
      if (method === 'sendJSON') {
        WSClient.__wrapped_send__ = true;
        if (typeof WSClient.send !== 'function' || WSClient.send === orig) {
          WSClient.send = WSClient.sendJSON;
        }
      }
    }

    maybeWrap();
    window.addEventListener('ws.open', maybeWrap);
  })();

  const STATIC_JS_BASE = (() => {
    if (typeof document === "undefined") {
      return "/static/js/";
    }

    const script = document.currentScript || document.querySelector('script[src$="/static/js/app.js"]');
    if (script && script.src) {
      try {
        const url = new URL(script.src, window.location.href);
        return url.pathname.replace(/[^/]+$/, "");
      } catch (err) {
        console.warn("Failed to parse static script URL", err);
      }
    }

    return "/static/js/";
  })();

  const VERSION_STAMP = (() => {
    if (typeof window !== "undefined" && typeof window.BUILD_ID === "string" && window.BUILD_ID) {
      return window.BUILD_ID;
    }
    if (typeof window !== "undefined") {
      const existing = window.__APP_VERSION_STAMP__;
      if (typeof existing === "string" && existing) {
        return existing;
      }
      const generated = Date.now().toString();
      try {
        window.__APP_VERSION_STAMP__ = generated;
      } catch (err) {
        // ignore inability to persist stamp on window
      }
      return generated;
    }
    return Date.now().toString();
  })();

  const VERSION_QUERY = (() => {
    const suffix = `?v=${encodeURIComponent(VERSION_STAMP)}`;
    if (typeof document === "undefined") {
      return suffix;
    }
    try {
      const current =
        document.currentScript ||
        document.querySelector('script[src*="/static/js/app.js"]') ||
        null;
      if (!current || !current.src) {
        return suffix;
      }
      const url = new URL(current.src, window.location.href);
      return url.search || suffix;
    } catch (err) {
      console.warn("Failed to derive version query", err);
      return suffix;
    }
  })();

  function isSameOrigin(url) {
    try {
      if (!/^(?:[a-z]+:)?\/\//i.test(url)) {
        return true;
      }
      const parsed = new URL(url, window.location.href);
      return parsed.origin === window.location.origin;
    } catch (err) {
      return false;
    }
  }

  function isSpecialScheme(url) {
    return /^(data:|blob:)/i.test(url);
  }

  function appendVersionQuery(url) {
    if (!VERSION_QUERY || typeof url !== "string" || url.includes("?")) {
      return url;
    }
    const hashIndex = url.indexOf("#");
    if (hashIndex === -1) {
      return `${url}${VERSION_QUERY}`;
    }
    return `${url.slice(0, hashIndex)}${VERSION_QUERY}${url.slice(hashIndex)}`;
  }

  function resolveScriptSrc(src) {
    if (!src || typeof src !== "string") {
      return src;
    }

    if (isSpecialScheme(src)) {
      return src;
    }

    if (/^(?:[a-z]+:)?\/\//i.test(src) || src.startsWith("/")) {
      if (!isSameOrigin(src)) {
        return src;
      }
      return appendVersionQuery(src);
    }

    const normalized = src.replace(/^\.\//, "");
    const base = STATIC_JS_BASE.replace(/\/?$/, "/");
    const joined = `${base}${normalized}`;

    return appendVersionQuery(joined);
  }

  function renderStatusBarFromState() {
    try {
      const appState = window.AppState || {};
      const snapshot = typeof appState.getState === "function" ? appState.getState() : appState;
      const recorder = snapshot.recorder && typeof snapshot.recorder === "object" ? snapshot.recorder : {};
      const merged = {
        ...snapshot,
        policy: snapshot.policy ?? (window.AppState?.policy || {}),
        wsConn: snapshot.wsConn ?? snapshot.wsConnected ?? appState.wsConnected,
        wsConning: snapshot.wsConning ?? snapshot.wsConnecting ?? appState.wsConnecting,
        asrReady: snapshot.asrReady ?? appState.asrReady,
        // Now only checks the single, unified 'listening' flag:
        micLive: snapshot.listening ?? appState.listening ?? window.AudioRecorder?.listening, 
        tts: snapshot.tts ?? snapshot.ttsActive ?? appState.tts,
        senderPaused: snapshot.senderPaused ?? appState.senderPaused,
        processing: snapshot.processing ?? appState.processing,
      };
      window.StatusBar?.render(merged);
    } catch {}
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      renderStatusBarFromState();
    });
  }

  window.AppUI = window.AppUI || {};
  window.AppUI.refresh = function () {
    renderStatusBarFromState();
  };

  if (typeof window !== "undefined") {
    window.addEventListener("client.log", (event) => {
      try {
        const detail = event?.detail;
        if (!detail || typeof detail !== "object") {
          return;
        }
        const AppState = window.AppState;
        if (!AppState || !AppState.hub || typeof AppState.hub.log !== "function") {
          return;
        }
        const label = typeof detail.label === "string" && detail.label ? detail.label : undefined;
        AppState.hub.log(label, detail.detail);
      } catch (err) {
        try {
          console.warn("client.log bridge failed", err);
        } catch {}
      }
    });
  }

  function loadScript(src) {
    const resolvedSrc = resolveScriptSrc(src);
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-dynamic="${resolvedSrc}"]`);
      if (existing) {
        if (existing.dataset.loaded === "true") {
          resolve();
        } else {
          existing.addEventListener("load", resolve, { once: true });
          existing.addEventListener("error", reject, { once: true });
        }
        return;
      }
      const el = document.createElement("script");
      el.src = resolvedSrc;
      el.type = "module";
      el.async = false;
      el.dataset.dynamic = resolvedSrc;
      el.addEventListener("load", () => {
        el.dataset.loaded = "true";
        resolve();
      }, { once: true });
      el.addEventListener("error", reject, { once: true });
      document.head.appendChild(el);
    });
  }

  async function bootstrapClientConfig() {
    if (typeof window === "undefined") {
      return {};
    }
    const existing = window.__CFG__ && typeof window.__CFG__ === "object"
      ? window.__CFG__
      : {};
    let merged = existing;
    try {
      const response = await fetch("/api/v1/admin/config", {
        method: "GET",
        credentials: "include"
      });
      if (response && response.ok) {
        try {
          const data = await response.json();
          if (data && typeof data === "object") {
            merged = Object.assign({}, existing, data);
            window.__CFG__ = merged;
            return merged;
          }
        } catch (err) {
          console.warn("Failed to parse admin config", err);
        }
      }
    } catch (err) {
      // Swallow network/config bootstrap failures silently; defaults will apply.
    }
    if (!window.__CFG__ || typeof window.__CFG__ !== "object") {
      window.__CFG__ = merged;
    }
    return window.__CFG__;
  }

  function diagHudEnabled() {
    if (typeof window === "undefined") {
      return false;
    }
    const cfg = window.__CFG__;
    if (cfg && typeof cfg.DIAG_CLIENT_HUD === "boolean") {
      return cfg.DIAG_CLIENT_HUD;
    }
    return false;
  }

  function toDiagString(value) {
    try {
      return String(value);
    } catch (err) {
      try {
        return Object.prototype.toString.call(value);
      } catch (innerErr) {
        return "";
      }
    }
  }

  function cloneDiagDetail(detail) {
    if (detail === undefined) {
      return undefined;
    }
    if (detail === null) {
      return null;
    }
    const valueType = typeof detail;
    if (valueType === "string" || valueType === "number" || valueType === "boolean") {
      return detail;
    }
    if (valueType === "object") {
      try {
        return JSON.parse(JSON.stringify(detail));
      } catch (err) {
        return { summary: toDiagString(detail) };
      }
    }
    return toDiagString(detail);
  }

  function getWsClient() {
    if (typeof window === "undefined") {
      return null;
    }
    return window.WSClient || null;
  }

  function getLiveSocket() {
    if (typeof WSClient !== 'undefined' && WSClient && WSClient._ws) {
      return WSClient._ws;
    }
    if (typeof AppState !== 'undefined' && AppState) {
      if (AppState.websocket) {
        return AppState.websocket;
      }
      if (typeof AppState.getState === 'function') {
        try {
          const state = AppState.getState();
          if (state && state.websocket) {
            return state.websocket;
          }
        } catch {}
      }
    }
    return null;
  }

  function wsClientIsConnected(wsClient) {
    if (!wsClient || typeof wsClient.send !== "function") {
      return false;
    }
    try {
      if (typeof wsClient.isConnected === "function") {
        return Boolean(wsClient.isConnected());
      }
    } catch (err) {
      // Ignore connectivity helper failures and fall back to socket state checks.
    }
    const socket = getLiveSocket();
    return Boolean(socket && socket.readyState === WebSocket.OPEN);
  }

  function sendDiagHudEvent(eventName, detail, options = {}) {
    if (!diagHudEnabled()) {
      return;
    }
    const wsClient = getWsClient();
    if (!wsClientIsConnected(wsClient)) {
      return;
    }
    const frame = {
      type: "client.diag",
      event: typeof eventName === "string" && eventName ? eventName.slice(0, 64) : "event",
      ts: Date.now()
    };
    const level = options && typeof options.level === "string" ? options.level.trim() : "";
    if (level) {
      frame.level = level.slice(0, 16);
    }
    const badge = options && typeof options.badge === "string" ? options.badge.trim() : "";
    if (badge) {
      frame.badge = badge.slice(0, 64);
    }
    if (options && typeof options.sample === "boolean") {
      frame.sample = options.sample;
    }
    const message = options && typeof options.message === "string" ? options.message.trim() : "";
    if (message) {
      frame.message = message.slice(0, 256);
    }
    const normalizedDetail = cloneDiagDetail(detail);
    if (normalizedDetail !== undefined) {
      frame.data = normalizedDetail;
    }
    try {
      wsClient.send(frame);
    } catch (err) {
      console.warn("Failed to send diag HUD event", err);
    }
  }

  const CLIENT_LOG_RATE_LIMIT_CAPACITY = 25;
  const CLIENT_LOG_RATE_LIMIT_WINDOW_SECONDS = 2.0;
  const CLIENT_LOG_RATE_LIMIT_WINDOW_MS = CLIENT_LOG_RATE_LIMIT_WINDOW_SECONDS * 1000;

  const clientLogRateLimiter = {
    tokens: CLIENT_LOG_RATE_LIMIT_CAPACITY,
    lastRefill: Date.now(),
  };

  function refillClientLogTokens(now) {
    const limiter = clientLogRateLimiter;
    const elapsed = now - limiter.lastRefill;
    if (!(elapsed > 0)) {
      return;
    }
    const tokensToAdd = (elapsed / CLIENT_LOG_RATE_LIMIT_WINDOW_MS) * CLIENT_LOG_RATE_LIMIT_CAPACITY;
    limiter.tokens = Math.min(
      CLIENT_LOG_RATE_LIMIT_CAPACITY,
      limiter.tokens + tokensToAdd
    );
    limiter.lastRefill = now;
  }

  function tryConsumeClientLogToken(count = 1) {
    const limiter = clientLogRateLimiter;
    const now = Date.now();
    refillClientLogTokens(now);
    if (limiter.tokens < count) {
      return false;
    }
    limiter.tokens -= count;
    return true;
  }

  function refundClientLogTokens(count = 1) {
    const limiter = clientLogRateLimiter;
    limiter.tokens = Math.min(
      CLIENT_LOG_RATE_LIMIT_CAPACITY,
      limiter.tokens + count
    );
  }

  function sendClientLog(label, detail) {
    const wsClient = getWsClient();
    if (!wsClientIsConnected(wsClient)) {
      return false;
    }
    if (!tryConsumeClientLogToken(1)) {
      return false;
    }
    const frame = {
      type: "client.log",
      label: typeof label === "string" && label ? label.slice(0, 64) : "event",
      ts: Date.now(),
    };
    const normalizedDetail = cloneDiagDetail(detail);
    if (normalizedDetail !== undefined) {
      frame.detail = normalizedDetail;
    }
    try {
      wsClient.send(frame);
      return true;
    } catch (err) {
      refundClientLogTokens(1);
      console.warn("Failed to send client.log event", err);
      return false;
    }
  }

  function diagChunkSampleN() {
    if (typeof window === "undefined") {
      return 10;
    }
    const cfg = window.__CFG__;
    const candidate = cfg ? Number(cfg.DIAG_CHUNK_SAMPLE_N) : NaN;
    if (Number.isFinite(candidate) && candidate > 0) {
      return Math.floor(candidate);
    }
    return 10;
  }

  const MIC_TELEMETRY_CHUNK_SAMPLE_BUDGET = 1;
  const MIC_TELEMETRY_HUB_SAMPLE_BUDGET = 1;
  const MIC_TELEMETRY_FIRST_CHUNK_BUDGET = 1;

  const micTelemetryState = {
    streaming: false,
    chunkLogBudget: 0,
    firstChunkLogBudget: 0,
    hubLogBudget: 0,
  };

  function beginMicTelemetrySession() {
    micTelemetryState.streaming = true;
    micTelemetryState.chunkLogBudget = MIC_TELEMETRY_CHUNK_SAMPLE_BUDGET;
    micTelemetryState.firstChunkLogBudget = MIC_TELEMETRY_FIRST_CHUNK_BUDGET;
    micTelemetryState.hubLogBudget = MIC_TELEMETRY_HUB_SAMPLE_BUDGET;
  }

  function endMicTelemetrySession() {
    micTelemetryState.streaming = false;
    micTelemetryState.chunkLogBudget = 0;
    micTelemetryState.firstChunkLogBudget = 0;
    micTelemetryState.hubLogBudget = 0;
  }

  function consumeMicTelemetryBudget(kind) {
    if (!micTelemetryState.streaming) {
      return true;
    }
    const key = `${kind}LogBudget`;
    if (!Object.prototype.hasOwnProperty.call(micTelemetryState, key)) {
      return false;
    }
    const remaining = micTelemetryState[key];
    if (typeof remaining !== "number" || remaining <= 0) {
      return false;
    }
    micTelemetryState[key] = remaining - 1;
    return true;
  }

  function setBadge(badge) {
    window.AppUI?.refresh?.();
  }

  let lastHudState = null;

  function handleHudStateChange(detail) {
    if (!diagHudEnabled()) {
      return;
    }
    const meta = detail && typeof detail === 'object' ? detail.meta : null;
    let stateLabel = typeof meta?.state === 'string' ? meta.state : null;
    if (!stateLabel && detail && typeof detail === 'object' && typeof detail.state === 'string') {
      stateLabel = detail.state;
    }
    if (!stateLabel) {
      return;
    }
    if (stateLabel === lastHudState) {
      return;
    }
    lastHudState = stateLabel;
    const isListening = stateLabel.toLowerCase() === 'listening';
    const badge = isListening ? 'mic:live' : 'mic:idle';
    const message = isListening ? 'Recorder active' : 'Recorder idle';
    setBadge(badge);
    sendDiagHudEvent(
      'EVT_CLIENT_HUD_STATE',
      detail,
      { level: 'info', badge, message }
    );
  }

  handleHudStateChange({ state: 'Idle', meta: { state: 'Idle', source: 'init' } });

  async function ensureRuntimeModules() {
    const needsAppStateStore = !window.AppState
      || typeof window.AppState.setState !== "function"
      || typeof window.AppState.getState !== "function";
    if (needsAppStateStore) {
      await loadScript("state.js");
    }
    if (!window.AudioPlayer) {
      await loadScript("audio_player.js");
    }
    if (!window.AudioRecorder) {
      await loadScript("audio/pcm_recorder.js");
    }
    await loadScript("audio/vad_client.js");
    if (!window.WSClient) {
      await loadScript("ws_client.js");
    }
    if (!window.TranscriptView) {
      await loadScript("transcript_view.js");
    }
    if (!window.WSErrorUI) {
      await loadScript("errors.js");
    }
  }

  async function init() {
    await bootstrapClientConfig();
    await ensureRuntimeModules();

    const urlParams = new URLSearchParams(window.location ? window.location.search : '');
    const AppState = window.AppState;
    const WSClient = window.WSClient;
    const audioRecorder = window.AudioRecorder || null;

    const deferredClientLogs = [];
    let deferredClientLogFlushTimer = null;

    const MAX_DEFERRED_CLIENT_LOGS = 64;
    const DEFERRED_CLIENT_LOG_FLUSH_DELAY_MS = 500;

    function scheduleDeferredClientLogFlush() {
      if (deferredClientLogFlushTimer || typeof setTimeout !== "function") {
        return;
      }
      deferredClientLogFlushTimer = setTimeout(() => {
        deferredClientLogFlushTimer = null;
        try {
          flushDeferredClientLogs();
        } catch (err) {
          try {
            console.warn("flushDeferredClientLogs failed", err);
          } catch (_) {}
        }
      }, DEFERRED_CLIENT_LOG_FLUSH_DELAY_MS);
    }

    function enqueueDeferredClientLog(label, detail) {
      if (!Array.isArray(deferredClientLogs)) {
        return;
      }
      const normalizedLabel = typeof label === "string" && label ? label : "event";
      const normalizedDetail = cloneDiagDetail(detail);
      if (deferredClientLogs.length >= MAX_DEFERRED_CLIENT_LOGS) {
        deferredClientLogs.shift();
      }
      deferredClientLogs.push({ label: normalizedLabel, detail: normalizedDetail });
      scheduleDeferredClientLogFlush();
    }

    function flushDeferredClientLogs() {
      if (!Array.isArray(deferredClientLogs) || !deferredClientLogs.length) {
        if (deferredClientLogFlushTimer) {
          clearTimeout(deferredClientLogFlushTimer);
          deferredClientLogFlushTimer = null;
        }
        return;
      }
      const wsClient = getWsClient();
      if (!wsClientIsConnected(wsClient)) {
        scheduleDeferredClientLogFlush();
        return;
      }
      while (deferredClientLogs.length) {
        const entry = deferredClientLogs[0];
        if (!sendClientLog(entry.label, entry.detail)) {
          scheduleDeferredClientLogFlush();
          return;
        }
        deferredClientLogs.shift();
      }
      if (deferredClientLogFlushTimer) {
        clearTimeout(deferredClientLogFlushTimer);
        deferredClientLogFlushTimer = null;
      }
    }

    // --- CRITICAL RECURSION GUARD: Defined in the outer scope of installHubInterface ---
    let __hubImplLogInFlight = false;

    function installHubInterface() {
      const hub = AppState && AppState.hub;
      if (!hub || typeof hub._install !== "function") {
        return;
      }

      const sendThroughPipe = (label, detail) => {
        // CRITICAL FIX: Schedule the send asynchronously to prevent synchronous errors/recursion
        if (typeof setTimeout !== "function") {
          if (!sendClientLog(label, detail)) {
            enqueueDeferredClientLog(label, detail);
          }
          return;
        }

        setTimeout(() => {
          try {
            if (!sendClientLog(label, detail)) {
              enqueueDeferredClientLog(label, detail);
            }
          } catch (err) {
            try {
              console.warn("Async sendThroughPipe failed", err);
            } catch (_) {}
          }
        }, 0);
      };

      const buildMirrorDetail = (detail, stageLabel) => {
        let payload;
        if (detail && typeof detail === "object") {
          payload = cloneDiagDetail(detail);
          if (!payload || typeof payload !== "object") {
            payload = { value: payload };
          }
        } else if (detail === undefined) {
          payload = {};
        } else if (typeof detail === "string") {
          payload = { message: detail };
        } else {
          payload = { value: detail };
        }
        if (payload && typeof payload === "object" && payload.stage_label === undefined) {
          payload.stage_label = stageLabel;
        }
        return payload;
      };

      let boundSocket = null;
      
      const hubImpl = {
        log(label, detail) {
          if (__hubImplLogInFlight) {
            return;
          }
          __hubImplLogInFlight = true;
          try {
            const normalizedLabel = typeof label === "string" && label ? label : "client.mic";
            let consoleText = normalizedLabel;
            let primaryDetail = detail;
            if (normalizedLabel === "client.mic" && typeof detail === "string") {
              consoleText = `${normalizedLabel} ${detail}`;
              primaryDetail = { message: consoleText };
            } else if (typeof detail === "string" && detail) {
              consoleText = `${normalizedLabel} ${detail}`;
            }
            if (consoleText) {
              try {
                console.log(consoleText);
              } catch {}
            }
            if (normalizedLabel === "client.mic") {
              if (consumeMicTelemetryBudget('hub')) {
                sendThroughPipe(normalizedLabel, primaryDetail);
              }
              return;
            }
            sendThroughPipe(normalizedLabel, primaryDetail);
            if (consumeMicTelemetryBudget('hub')) {
              const mirrorDetail = buildMirrorDetail(detail, normalizedLabel);
              sendThroughPipe("client.mic", mirrorDetail);
            }
          } catch (err) {
            console.warn("Hub log failed: synchronous recursion detected and caught", err);
          } finally {
            __hubImplLogInFlight = false;
          }
        },
        bindSocket(ws) {
          const next = ws || null;
          if (boundSocket === next) {
            return;
          }
          boundSocket = next;
          if (audioRecorder && typeof audioRecorder.setSocket === "function") {
            try {
              audioRecorder.setSocket(next);
            } catch (err) {
              console.warn("AudioRecorder bindSocket failed", err);
            }
          }
          const outcome = next ? "bound" : "cleared";
          // This call triggers the log path:
          hubImpl.log("client.ws", { outcome, source: "hub.bindSocket" });
        },
        startListening(policy) {
          if (!audioRecorder || typeof audioRecorder.startListening !== "function") {
            return undefined;
          }
          try {
            return audioRecorder.startListening(policy || {});
          } catch (err) {
            console.warn("AudioRecorder startListening failed", err);
            return undefined;
          }
        },
        stopListening(reason) {
          if (!audioRecorder || typeof audioRecorder.stopListening !== "function") {
            return;
          }
          try {
            if (reason && typeof reason === "object") {
              audioRecorder.stopListening(reason);
            } else if (reason !== undefined) {
              audioRecorder.stopListening({ reason });
            } else {
              audioRecorder.stopListening();
            }
          } catch (err) {
            console.warn("AudioRecorder stopListening failed", err);
          }
        },
      };

      hub._install(hubImpl);
    }

    installHubInterface();

    let policyCaptureLogged = false;

    renderStatusBarFromState();
    if (typeof AppState?.on === 'function') {
      // Listeners for refreshing the UI upon state changes (simplified to use AppState.listening)
      const events = ['change', 'wsConnected', 'ttsActive', 'asrReady', 'listening'];
      events.forEach((eventName) => AppState.on(eventName, () => window.AppUI?.refresh?.()));
    }
    

    const CLIENT_MIC_OPEN_EVENT = 'EVT_CLIENT_MIC_OPEN';
    const CLIENT_HUD_STATE_EVENT = 'EVT_HUD_STATE';

    function normalizeStopReason(source) {
      if (typeof source === 'string' && source) {
        return source.slice(0, 48);
      }
      if (source && typeof source === 'object') {
        if (typeof source.reason === 'string' && source.reason) {
          return source.reason.slice(0, 48);
        }
        if (typeof source.code === 'string' && source.code) {
          return source.code.slice(0, 48);
        }
      }
      return 'unknown';
    }

    function isRecorderListening() {
      // Simplified: now only checks the single authoritative state flag
      let stateSnapshot = null;
      try {
        stateSnapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
      } catch (_) {
        stateSnapshot = AppState;
      }
      if (stateSnapshot && typeof stateSnapshot.listening === "boolean") {
        return stateSnapshot.listening;
      }
      // Fallback to hardware check if state is missing
      return !!window.AudioRecorder?.listening;
    }

    function logClientMicEventText(text) {
      try {
        console.log(text);
      } catch {}
      hubLog('client.mic', { text });
    }

    function updateRecordingState(active, source) {
      const previous = isRecorderListening();
      const next = !!active;

      // *** Primary Action: Update the Single Source of Truth ***
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ listening: next });
      } else if (typeof AppState === 'object' && AppState) {
        AppState.listening = next;
      }

      let stopReasonLabel = null;
      if (next) {
        if (!previous) {
          // REMOVED: AppState.__no_rearm_until = 0;
          beginMicTelemetrySession();
          // REMOVED: Policy check from getCaptureTimesliceMs()
          logClientMicEventText('evt=mic_start timeslice_ms=unknown'); 
        }
      } else if (previous) {
        stopReasonLabel = normalizeStopReason(source);
        logClientMicEventText(`evt=mic_stop reason=${stopReasonLabel}`);
        endMicTelemetrySession();
      }

      if (previous !== next) {
        const hudState = next ? 'Listening' : 'Idle';
        const hudMeta = { state: hudState };
        if (typeof source === 'string' && source) {
          hudMeta.source = source;
        } else if (!next && stopReasonLabel) {
          hudMeta.reason = stopReasonLabel;
        }
        handleHudStateChange({ state: hudState, meta: hudMeta });
      }

      window.AppUI?.refresh?.();
      return next;
    }

    function startMicCapture() {
      if (!audioRecorder || typeof audioRecorder.startMicCaptureIfIdle !== 'function') {
        return Promise.reject(new Error('startMicCapture_unavailable'));
      }
      let outcome;
      try {
        outcome = audioRecorder.startMicCaptureIfIdle();
      } catch (err) {
        return Promise.reject(err);
      }
      if (outcome && typeof outcome.then === 'function') {
        return outcome.then((value) => {
          if (value) {
            setMicPermissionGranted(true, 'startMicCapture');
          }
          return value;
        });
      }
      if (outcome) {
        setMicPermissionGranted(true, 'startMicCapture');
      }
      return Promise.resolve(outcome);
    }

    function setMicPermissionGranted(granted) {
      const next = !!granted;
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ micPermissionGranted: next });
      } else if (AppState && typeof AppState === 'object') {
        AppState.micPermissionGranted = next;
      }
      return next;
    }

    // REMOVED: startClientReadyTracking(), recordClientReadyEvent(), flushPendingClientReady()

    window.addEventListener(CLIENT_MIC_OPEN_EVENT, (event) => {
      setMicPermissionGranted(true, 'mic_open_event');
      updateRecordingState(true, 'mic_open_event');
    });

    window.addEventListener(CLIENT_HUD_STATE_EVENT, (event) => {
      handleHudStateChange(event && event.detail);
    });

    window.addEventListener('input.start', () => {
      updateRecordingState(true, 'input.start');
      setMicPermissionGranted(true, 'input.start');
    });

    window.addEventListener('input.stop', () => {
      updateRecordingState(false, 'input.stop');
    });

    window.addEventListener('stop_listening', (event) => {
      const reason =
        (event && event.detail && typeof event.detail.reason === 'string' && event.detail.reason) ||
        'stop_listening';
      updateRecordingState(false, reason);
    });

    // START REWRITE: window.addEventListener('ws.close', ...)
    window.addEventListener('ws.close', () => {
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ asrReady: false, turnState: null, policy: null });
      } else {
        if (AppState) {
          AppState.asrReady = false;
          AppState.turnState = null;
          AppState.policy = null;
        }
      }
      // *** Crucial Fix: Forcibly stop mic hardware and clear UI state ***
      updateRecordingState(false, 'ws.close');
      // END Crucial Fix
      setMicPermissionGranted(false, 'ws.close');
      policyCaptureLogged = false;
      window.AppUI?.refresh?.();
    });
    // END REWRITE: window.addEventListener('ws.close', ...)

    window.addEventListener('ws.open', () => {
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ asrReady: false, turnState: null });
      } else if (AppState) {
        AppState.asrReady = false;
        AppState.turnState = null;
      }
      updateRecordingState(false, 'ws.open');
      policyCaptureLogged = false;
      if (getLiveSocket()) {
        try {
          flushDeferredClientLogs();
        } catch {}
      }
      window.AppUI?.refresh?.();
    });

    // --- App context (server-injected) ---
    function readAppContext() {
      const node = document.getElementById('appContext');
      if (!node) return {};
      try {
        const raw = node.textContent || node.innerText || '{}';
        return JSON.parse(raw);
      } catch (err) {
        console.warn('Failed to parse app context', err);
        return {};
      }
    }

    const serverContext = readAppContext();
    const currentUserEmail = typeof serverContext.userEmail === 'string' && serverContext.userEmail
      ? serverContext.userEmail
      : 'user@example.com';
    const isAdmin = Boolean(serverContext.isAdmin);
    window.__ASKCHIP_CTX__ = Object.freeze({
      isAdmin,
      userEmail: currentUserEmail,
    });

    // --- Top-right brand dropdown ---
    const brandBtn = document.getElementById('brandBtn');
    const brandMenu = document.getElementById('brandMenu');
    const adminItem = document.getElementById('adminItem');
    brandMenu.setAttribute('aria-hidden', 'true');
    function syncAdminItem() {
      if (isAdmin) {
        adminItem.removeAttribute('aria-disabled');
        adminItem.disabled = false;
        adminItem.tabIndex = 0;
        adminItem.title = "Open Admin UI";
      } else {
        adminItem.setAttribute('aria-disabled', 'true');
        adminItem.disabled = true;
        adminItem.tabIndex = -1;
        adminItem.title = "Admins only";
      }
    }
    syncAdminItem();
    function closeMenu() {
      brandMenu.classList.remove('open');
      brandMenu.setAttribute('aria-hidden', 'true');
      brandBtn.setAttribute('aria-expanded', 'false');
    }
    brandBtn.addEventListener('click', (e) => {
      const open = brandMenu.classList.toggle('open');
      brandBtn.setAttribute('aria-expanded', String(open));
      brandMenu.setAttribute('aria-hidden', String(!open));
      if (!open) return;
      const firstItem = brandMenu.querySelector('.menu-item:not([aria-disabled="true"])');
      if (firstItem) firstItem.focus();
    });
    document.addEventListener('click', (e) => {
      if (!brandMenu.contains(e.target) && !brandBtn.contains(e.target)) closeMenu();
    });
    brandMenu.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeMenu();
        brandBtn.focus();
      }
    });
    adminItem.addEventListener('click', (event) => {
      if (!isAdmin) {
        event.preventDefault();
        return;
      }
      window.location.href = '/admin/logs';
      closeMenu();
    });
    document.getElementById('profileItem').addEventListener('click', () => {
      alert(`Profile for ${currentUserEmail} (placeholder).`); closeMenu();
    });
    document.getElementById('logoutItem').addEventListener('click', () => {
      alert("Logging out… (placeholder)"); closeMenu();
    });    

    // --- Chat toggle ---
    const openChatBtn = document.getElementById('openChatBtn');
    const chatPanel = document.querySelector('.chat');
    openChatBtn.addEventListener('click', () => {
      const hidden = chatPanel.classList.toggle('hidden');
      openChatBtn.setAttribute('aria-pressed', String(!hidden));
    });

    // --- Start/End button wiring ---
    const startBtn = document.getElementById('startBtn');
    const endBtn = document.getElementById('endBtn');
    const sidText = document.getElementById('sid-text');
    const latencyHint = document.getElementById('latencyHint');
    const voiceLabel = document.getElementById('voiceLabel');
    const localeLabel = document.getElementById('localeLabel');
    const textChatForm = document.getElementById('textChatForm');
    const textChatInput = document.getElementById('textChatInput');

    // Chat submit wiring
    // Send typed chat to the server using chat.user frames
    if (textChatForm) {
      textChatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = (textChatInput && textChatInput.value || '').trim();
        if (!text) return;
        try {
          if (WSClient && typeof WSClient.sendJSON === 'function') {
            WSClient.sendJSON({ type: 'chat.user', text });
          } else if (WSClient && typeof WSClient.send === 'function') {
            WSClient.send({ type: 'chat.user', text });
          }
          textChatInput.value = '';
        } catch (err) {
          console.error('chat.user send failed', err);
        }
      });
    }
    

    const voiceState = { voiceId: null, locale: null };

    const modeSuggestionsEl = document.getElementById('modeSuggestions');
    const modeSuggestionsLabelEl = document.getElementById('modeSuggestionsLabel');
    const modeSuggestionsChipsEl = document.getElementById('modeSuggestionsChips');
    const modeSuggestionsState = { mode: null, items: [] };

    function formatModeLabel(value) {
      if (typeof value !== 'string') return '';
      const normalized = value.trim().replace(/[_\s]+/g, ' ');
      if (!normalized) return '';
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }

    function resetSuggestions() {
      modeSuggestionsState.mode = null;
      modeSuggestionsState.items = [];
      renderSuggestions();
    }

    function renderSuggestions() {
      if (!modeSuggestionsEl || !modeSuggestionsChipsEl) {
        return;
      }
      const items = Array.isArray(modeSuggestionsState.items)
        ? modeSuggestionsState.items
        : [];
      const hasItems = items.length > 0;
      modeSuggestionsEl.classList.toggle('hidden', !hasItems);
      modeSuggestionsChipsEl.replaceChildren();
      if (!hasItems) {
        if (modeSuggestionsLabelEl) {
          modeSuggestionsLabelEl.textContent = '';
        }
        return;
      }
      for (const label of items) {
        const chip = document.createElement('span');
        chip.className = 'mode-chip';
        chip.textContent = label;
        chip.setAttribute('role', 'listitem');
        modeSuggestionsChipsEl.appendChild(chip);
      }
      if (modeSuggestionsLabelEl) {
        const formatted = formatModeLabel(modeSuggestionsState.mode);
        modeSuggestionsLabelEl.textContent = formatted
          ? `${formatted} suggestions`
          : 'Suggested actions';
      }
    }

    function applySuggestionsFrame(detail) {
      if (!detail || typeof detail !== 'object') {
        resetSuggestions();
        return;
      }
      const rawItems = Array.isArray(detail.items) ? detail.items : [];
      const cleaned = [];
      for (const item of rawItems) {
        if (!item || typeof item !== 'object') continue;
        const label = typeof item.label === 'string' ? item.label.trim() : '';
        if (!label) continue;
        cleaned.push(label);
      }
      modeSuggestionsState.mode = typeof detail.mode === 'string' ? detail.mode : null;
      modeSuggestionsState.items = cleaned;
      renderSuggestions();
    }

    function renderVoiceState() {
      if (voiceLabel) {
        voiceLabel.textContent = voiceState.voiceId || '—';
      }
      if (localeLabel) {
        localeLabel.textContent = voiceState.locale || '—';
      }
    }

    function resetVoiceState() {
      voiceState.voiceId = null;
      voiceState.locale = null;
      renderVoiceState();
    }

    function updateVoiceState(partial) {
      if (!partial || typeof partial !== 'object') {
        return;
      }
      let updated = false;
      if (typeof partial.voiceId === 'string') {
        const trimmed = partial.voiceId.trim();
        if (trimmed && trimmed !== voiceState.voiceId) {
          voiceState.voiceId = trimmed;
          updated = true;
        }
      }
      if (typeof partial.locale === 'string') {
        const trimmed = partial.locale.trim();
        if (trimmed && trimmed !== voiceState.locale) {
          voiceState.locale = trimmed;
          updated = true;
        }
      }
      if (updated) {
        renderVoiceState();
      }
    }

    function extractVoiceLocale(frame) {
      if (!frame || typeof frame !== 'object') {
        return {};
      }
      let voiceId = null;
      let locale = null;
      if (typeof frame.voice_id === 'string') {
        voiceId = frame.voice_id;
      }
      if (typeof frame.locale === 'string') {
        locale = frame.locale;
      }
      const meta = frame.meta && typeof frame.meta === 'object' ? frame.meta : null;
      if (meta) {
        if (!voiceId && typeof meta.voice_id === 'string') {
          voiceId = meta.voice_id;
        }
        if (!locale && typeof meta.locale === 'string') {
          locale = meta.locale;
        }
        const ttsMeta = meta.tts && typeof meta.tts === 'object' ? meta.tts : null;
        if (ttsMeta) {
          if (!voiceId && typeof ttsMeta.voice_id === 'string') {
            voiceId = ttsMeta.voice_id;
          }
          if (!locale && typeof ttsMeta.locale === 'string') {
            locale = ttsMeta.locale;
          }
        }
      }
      return { voiceId, locale };
    }

    renderVoiceState();
    renderSuggestions();

    function isInteractiveTarget(target) {
      if (!target || target === document.body || target === document.documentElement) {
        return false;
      }
      if (target.isContentEditable) {
        return true;
      }
      if (!(target instanceof HTMLElement)) {
        return false;
      }
      const tag = target.tagName;
      if (tag) {
        const normalized = tag.toUpperCase();
        if (normalized === 'INPUT' || normalized === 'TEXTAREA' || normalized === 'SELECT' || normalized === 'BUTTON') {
          return true;
        }
      }
      if (target.closest('input, textarea, select, button, a[href], [role="textbox"], [role="button"], [role="menuitem"]')) {
        return true;
      }
      return false;
    }

    function handleGlobalKeyDown(event) {
      if (event.defaultPrevented) return;
      const { key, ctrlKey, metaKey, altKey } = event;
      const isModifier = ctrlKey || metaKey || altKey;
      if (key === 'Enter' && !isModifier) {
        if (isInteractiveTarget(event.target)) {
          return;
        }
        if (textChatInput) {
          const value = textChatInput.value || '';
          if (value.trim()) {
            event.preventDefault();
            if (textChatForm && typeof textChatForm.requestSubmit === 'function') {
              textChatForm.requestSubmit();
            } else if (textChatForm) {
              textChatForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
          } else {
            event.preventDefault();
            textChatInput.focus();
          }
        }
        return;
      }
      if (key === 'Escape') {
        if (brandMenu && brandMenu.classList.contains('open')) {
          event.preventDefault();
          closeMenu();
          brandBtn.focus();
          return;
        }
        if (isInteractiveTarget(event.target)) {
          return;
        }
        return;
      }
    }

    window.addEventListener('keydown', handleGlobalKeyDown, true);

    const showToastMessage = (() => {
      let root = null;
      const styleId = 'inline-toast-styles';
      const styleText = '#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}';
      function ensureRoot() {
        root = root && root.isConnected ? root : document.getElementById('toast-root');
        if (!root) {
          root = document.createElement('div');
          root.id = 'toast-root';
          root.className = 'toast-container';
          document.body.appendChild(root);
        }
        if (!document.getElementById(styleId)) {
          document.head.appendChild(Object.assign(document.createElement('style'), { id: styleId, textContent: styleText }));
        }
        return root;
      }
      return (message) => {
        if (!message) return;
        const host = ensureRoot();
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.setAttribute('role', 'alert'); toast.innerHTML = '<div class="toast-body"></div>';
        toast.firstChild.textContent = message;
        host.appendChild(toast);
        setTimeout(() => {
          toast.classList.add('toast-exit');
          setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 220);
        }, 3600);
      };
    })();

    let lastMicNudgeTs = 0;
    window.addEventListener('hud.nudge', (event) => {
      const detail = event && event.detail;
      if (!detail || detail.code !== 'mic_permissions') {
        return;
      }
      const now = Date.now();
      if (now - lastMicNudgeTs < 1800) {
        return;
      }
      lastMicNudgeTs = now;
      const reason = typeof detail.reason === 'string' && detail.reason
        ? detail.reason
        : 'mic_open_timeout';
      const message = reason === 'mic_open_timeout'
        ? "We haven't heard from your microphone yet. Check your browser permissions."
        : "We couldn't access your microphone. Please review your browser permissions.";
      showToastMessage(message);
      sendDiagHudEvent(
        'EVT_CLIENT_MIC_PERMISSION_NUDGE',
        { reason },
        { level: 'info', badge: 'mic:nudge', message: 'Prompted user to review mic permissions' }
      );
    });

    function csrf() {
      const cookies = document.cookie ? document.cookie.split(';') : [];
      for (const name of ['askchip_csrf', 'csrftoken', 'csrf_token']) {
        const prefix = `${name}=`;
        for (const part of cookies) {
          const trimmed = part.trim();
          if (!trimmed.startsWith(prefix)) continue;
          const raw = trimmed.slice(prefix.length);
          if (!raw) continue;
          try { return decodeURIComponent(raw); } catch (err) {
            console.warn('Failed to decode CSRF cookie', err);
            return raw;
          }
        }
      }
      return '';
    }

    async function getMe() {
      try {
        const r = await fetch('/api/v1/auth/me', { method: 'GET', credentials: 'include' });
        if (!r.ok) return { authenticated: false };
        return await r.json();
      } catch {
        return { authenticated: false };
      }
    }

    async function mintWsToken() {
      const headers = {};
      if (typeof csrf === 'function') headers['X-CSRF-Token'] = csrf();
      try {
        const r = await fetch('/api/v1/auth/ws-token', {
          method: 'POST',
          headers,
          credentials: 'include',
        });
        if (!r.ok) {
          return { ok: false, status: r.status };
        }
        try {
          const body = await r.json();
          return { ok: true, body };
        } catch (err) {
          console.error('Failed to parse ws-token response', err);
          return { ok: false, status: r.status };
        }
      } catch (err) {
        console.error('Failed to mint ws-token', err);
        return { ok: false, status: 0 };
      }
    }

    function rememberToken(sid, ttl_ms) {
      if (!window.AppState || typeof window.AppState.setState !== 'function') return;
      window.AppState.setState({
        sid,
        wsTokenIssuedAt: Date.now(),
        wsTokenTTL: ttl_ms
      });
    }

    function tokenFreshEnough() {
      const s = window.AppState && window.AppState.getState ? window.AppState.getState() : null;
      if (!s || !s.wsTokenIssuedAt || !s.wsTokenTTL) return false;
      const age = Date.now() - s.wsTokenIssuedAt;
      return age <= (s.wsTokenTTL - 1500);
    }

    function showStartToast(msg) {
      if (!msg) return;
      if (typeof showToast === 'function') {
        showToast(msg);
      } else if (typeof showToastMessage === 'function') {
        showToastMessage(msg);
      } else if (typeof alert === 'function') {
        alert(msg);
      } else {
        console.warn('Toast:', msg);
      }
    }

    function showLoginModal() {
      if (window.AuthUI && typeof window.AuthUI.showLoginModal === 'function') {
        window.AuthUI.showLoginModal();
      }
    }

    function showProfileModal() {
      if (window.AuthUI && typeof window.AuthUI.showProfileModal === 'function') {
        window.AuthUI.showProfileModal();
      }
    }

    async function handleStartSessionClick() {
      try {
        const me = await getMe();
        if (!me.authenticated) {
          if (typeof showLoginModal === 'function') showLoginModal();
          showStartToast('Please login first.');
          return;
        }
        if (me.profile_complete === false) {
          if (typeof showProfileModal === 'function') showProfileModal();
          showStartToast('Complete your profile to continue.');
          return;
        }
      } catch {
      }

      // 1) If a previously minted token is still fresh, you MAY skip re-minting.
      // For reliability we mint a fresh token every Start click. If you prefer
      // to skip re-minting when fresh, wrap mint with `if (!tokenFreshEnough()) {...}`
      const minted = await mintWsToken();
      if (!minted.ok) {
        if (minted.status === 401 || minted.status === 403) {
          showStartToast('Please login and complete your profile.');
        } else if (minted.status === 409) {
          showStartToast('Profile required. Please complete your profile.');
        } else {
          showStartToast('Could not start session. Try again.');
        }
        return;
      }

      const { access_token, sid, ttl_ms } = minted.body || {};
      if (!access_token || !sid || !ttl_ms) {
        console.error('ws-token response missing fields', minted.body);
        showStartToast('Could not start session. Try again.');
        return;
      }

      const ttlValue = Number(ttl_ms);
      if (!Number.isFinite(ttlValue) || ttlValue <= 0) {
        console.error('ws-token ttl invalid', ttl_ms);
        showStartToast('Could not start session. Try again.');
        return;
      }

      rememberToken(sid, ttlValue);

      if (!tokenFreshEnough()) {
        console.error('ws-token considered stale', { sid, ttl_ms: ttlValue });
        showStartToast('Could not start session. Try again.');
        return;
      }

      const params = new URLSearchParams({ access_token });
      const state = AppState && typeof AppState.getState === 'function' ? AppState.getState() : null;
      if (state && state.resume && typeof state.resume.token === 'string') {
        const resume = state.resume;
        if (Number.isFinite(resume.expiresAt) && Date.now() < resume.expiresAt) {
          params.set('resume', resume.token);
        }
      }
      let wsPath = '/ws/v2/chat';
      try {
        const routing = AppState?.policy?.policy?.routing;
        const candidate = typeof routing?.ws_version === 'string' ? routing.ws_version.trim() : '';
        if (candidate && candidate.toLowerCase() !== 'v2') {
          console.warn('Unsupported ws_version from policy; forcing v2', candidate);
        }
      } catch (err) {
        console.warn('Failed to inspect policy routing for ws path', err);
      }
      const wsUrl = `${wsPath}?${params.toString()}`;

      // Add token as SECOND subprotocol so the server can read it if a proxy
      // or any client path drops the query string.
      const subprotocols = ['chat.v2', `jwt.${access_token}`];

      console.log('evt=ws_open_attempt', { url: wsUrl, protocols: subprotocols, sid });

      try {
        WSClient.open(wsUrl, subprotocols);
      } catch (err) {
        console.error('WSClient.open failed', err);
        if (AppState && typeof AppState.setState === 'function') {
          AppState.setState({ connectionState: 'disconnected' });
        }
        showStartToast('Could not open a session. Please try again.');
      }
    }

    startBtn.addEventListener('click', (event) => {
      event.preventDefault();
      if (startBtn.disabled) return;
      startBtn.disabled = true;
      Promise.resolve(handleStartSessionClick()).finally(() => {
        startBtn.disabled = false;
      });
    });

    endBtn.addEventListener('click', () => {
      WSClient.close('user_requested');
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      if (audioRecorder && typeof audioRecorder.stop === 'function') {
        try {
          audioRecorder.stop();
        } catch (err) {
          console.warn('Failed to stop audio recorder', err);
        }
      }
      updateRecordingState(false, 'end_button');
      if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
        window.AudioPlayer.interrupt();
      }
    });

    // --- Resume banner ---
    const resumeBanner = document.createElement('div');
    resumeBanner.setAttribute('role', 'status');
    resumeBanner.setAttribute('aria-live', 'polite');
    resumeBanner.style.position = 'fixed';
    resumeBanner.style.top = '16px';
    resumeBanner.style.right = '16px';
    resumeBanner.style.display = 'none';
    resumeBanner.style.alignItems = 'center';
    resumeBanner.style.gap = '12px';
    resumeBanner.style.padding = '10px 14px';
    resumeBanner.style.borderRadius = '10px';
    resumeBanner.style.background = 'rgba(12, 19, 35, 0.92)';
    resumeBanner.style.color = '#fff';
    resumeBanner.style.fontSize = '13px';
    resumeBanner.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.25)';
    resumeBanner.style.zIndex = '1000';
    resumeBanner.style.backdropFilter = 'blur(8px)';
    resumeBanner.style.webkitBackdropFilter = 'blur(8px)';

    const resumeText = document.createElement('span');
    resumeText.textContent = '';

    const resumeAction = document.createElement('button');
    resumeAction.type = 'button';
    resumeAction.textContent = 'Start new session';
    resumeAction.style.background = '#2251ff';
    resumeAction.style.color = '#fff';
    resumeAction.style.border = 'none';
    resumeAction.style.borderRadius = '6px';
    resumeAction.style.padding = '6px 10px';
    resumeAction.style.fontSize = '12px';
    resumeAction.style.cursor = 'pointer';
    resumeAction.style.fontWeight = '600';

    resumeBanner.append(resumeText, resumeAction);
    document.body.appendChild(resumeBanner);

    let resumeBannerMode = 'hidden';
    let resumeCountdownId = null;

    function stopResumeCountdown() {
      if (resumeCountdownId) {
        clearInterval(resumeCountdownId);
        resumeCountdownId = null;
      }
    }

    function hideResumeBanner() {
      if (resumeBannerMode === 'hidden') return;
      stopResumeCountdown();
      resumeBannerMode = 'hidden';
      resumeBanner.style.display = 'none';
    }

    function renderResumeCountdown() {
      const state = AppState.getState();
      const resume = state && typeof state.resume === 'object' ? state.resume : null;
      if (!resume || !Number.isFinite(resume.expiresAt)) {
        return false;
      }
      const remainingMs = Math.max(0, resume.expiresAt - Date.now());
      const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
      resumeText.textContent = `Reconnecting… (${seconds}s)`;
      return true;
    }

    function showResumeCountdown() {
      if (!renderResumeCountdown()) {
        hideResumeBanner();
        return;
      }
      if (resumeBannerMode !== 'countdown') {
        stopResumeCountdown();
        resumeBannerMode = 'countdown';
        resumeBanner.style.display = 'flex';
        resumeAction.disabled = false;
        resumeCountdownId = setInterval(() => {
          if (!renderResumeCountdown()) {
            hideResumeBanner();
          }
        }, 1000);
      }
    }

    function showResumeError() {
      stopResumeCountdown();
      resumeBannerMode = 'error';
      resumeBanner.style.display = 'flex';
      resumeText.textContent = 'Session resume unavailable. Start a new session to continue.';
      resumeAction.disabled = false;
    }

    function updateResumeBanner(state) {
      const hasCountdown = state.connectionState === 'resuming' && state.resume && Number.isFinite(state.resume.expiresAt);
      if (hasCountdown) {
        showResumeCountdown();
        return;
      }
      if (state.resumeError === 'invalid') {
        showResumeError();
        return;
      }
      hideResumeBanner();
    }

    resumeAction.addEventListener('click', () => {
      resumeAction.disabled = true;
      try {
        WSClient.close('user_restart');
      } catch (err) {
        console.warn('Resume banner close failed', err);
      }
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      AppState.setState({ resumeError: null });
      Promise.resolve(handleStartSessionClick()).catch((err) => {
        console.error('Failed to start new session', err);
        AppState.setState({ connectionState: 'disconnected' });
      }).finally(() => {
        resumeAction.disabled = false;
      });
    });

    // --- Waveform visual inside the Chip window ---
    const Waveform = (() => {
      const canvas = document.getElementById('waveCanvas');
      const ctx = canvas.getContext('2d', { alpha: false });
      const bgCanvas = document.createElement('canvas');
      const bgCtx = bgCanvas.getContext('2d', { alpha: false });
      let raf = 0,
        analyser = null,
        source = null,
        audioCtx = null,
        dataArray = null,
        prevAnalyserSnapshot = null;
      let synthMode = true,
        t = 0,
        lastDrawTime = 0;
      const FRAME_INTERVAL = 1000 / 30;
      let cachedDpr = 1,
        bgPixelWidth = 0,
        bgPixelHeight = 0;

      function redrawBackgroundPattern() {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        if (!w || !h) {
          return;
        }
        bgCtx.clearRect(0, 0, w, h);
        const gradient = bgCtx.createLinearGradient(0, 0, w, h);
        gradient.addColorStop(0, '#0b1222');
        gradient.addColorStop(1, '#0a0f1b');
        bgCtx.fillStyle = gradient;
        bgCtx.fillRect(0, 0, w, h);
        bgCtx.strokeStyle = 'rgba(255,255,255,0.04)';
        bgCtx.lineWidth = 1;
        const gap = 24;
        bgCtx.beginPath();
        for (let x = 0; x < w; x += gap) {
          bgCtx.moveTo(x, 0);
          bgCtx.lineTo(x, h);
        }
        for (let y = 0; y < h; y += gap) {
          bgCtx.moveTo(0, y);
          bgCtx.lineTo(w, y);
        }
        bgCtx.stroke();
      }

      function ensureBackground(forceRedraw = false) {
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        const cssWidth = canvas.clientWidth;
        const cssHeight = canvas.clientHeight;
        const pixelWidth = Math.max(1, Math.round(cssWidth * dpr));
        const pixelHeight = Math.max(1, Math.round(cssHeight * dpr));
        const sizeChanged =
          pixelWidth !== bgPixelWidth || pixelHeight !== bgPixelHeight || dpr !== cachedDpr;
        if (sizeChanged) {
          cachedDpr = dpr;
          bgPixelWidth = pixelWidth;
          bgPixelHeight = pixelHeight;
          canvas.width = pixelWidth;
          canvas.height = pixelHeight;
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          bgCanvas.width = pixelWidth;
          bgCanvas.height = pixelHeight;
          bgCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
          forceRedraw = true;
        }
        if (forceRedraw) {
          redrawBackgroundPattern();
        }
      }

      function resizeCanvas() {
        ensureBackground(true);
        drawBackground();
        if (synthMode) {
          drawSynth();
        } else {
          drawAnalyser(prevAnalyserSnapshot || dataArray);
        }
      }
      window.addEventListener('resize', resizeCanvas, { passive: true });
      resizeCanvas();

      function drawBackground() {
        ensureBackground(false);
        ctx.drawImage(bgCanvas, 0, 0, canvas.clientWidth, canvas.clientHeight);
      }

      function drawSynth() {
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const cx = w/2, cy = h/2; const amp = Math.min(120, h*0.28);
        const bars = 96;
        ctx.save();
        ctx.translate(0, cy);
        const hueA = 22;
        const hueB = 204;
        for(let i=0;i<bars;i++){
          const k = i/(bars-1);
          const phase = t*0.015 + k*6.283;
          const value = Math.sin(phase) * Math.sin(k*Math.PI);
          const y = value * amp * (0.85 + 0.15*Math.sin(t*0.01 + k*12));
          const x = k * w;
          const hue = hueA*(1-k) + hueB*k;
          ctx.strokeStyle = `hsla(${hue}, 85%, ${40 + 15*Math.sin(t*0.02 + k*5)}%, 0.9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, y);
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, -y*0.6);
          ctx.stroke();
        }
        ctx.restore();
        t += 1;
      }

      function drawAnalyser(currentData){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!currentData || !currentData.length){ drawSynth(); return; }
        const bars = 96;
        const step = Math.max(1, Math.floor(currentData.length / bars));
        ctx.save();
        ctx.translate(0, h/2);
        for(let i=0;i<bars;i++){
          const v = currentData[i*step] / 255;
          const y = (v*v) * (h*0.35);
          const k = i/(bars-1);
          ctx.strokeStyle = `hsla(${22*(1-k) + 204*k}, 85%, ${45 + v*20}%, .9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), y); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), -y*0.6); ctx.stroke();
        }
        ctx.restore();
      }

      function loop(now = (typeof performance !== 'undefined' ? performance.now() : Date.now())){
        raf = requestAnimationFrame(loop);
        const elapsed = now - lastDrawTime;
        let dataChanged = synthMode;
        let currentData = null;

        if (!synthMode && analyser && dataArray){
          analyser.getByteFrequencyData(dataArray);
          currentData = dataArray;
          if (!prevAnalyserSnapshot || prevAnalyserSnapshot.length !== dataArray.length){
            prevAnalyserSnapshot = new Uint8Array(dataArray.length);
            prevAnalyserSnapshot.set(dataArray);
            dataChanged = true;
          } else {
            const sampleStep = Math.max(1, Math.floor(dataArray.length / 32));
            for(let i=0;i<dataArray.length;i+=sampleStep){
              if (prevAnalyserSnapshot[i] !== dataArray[i]){ dataChanged = true; break; }
            }
            if (dataChanged){
              prevAnalyserSnapshot.set(dataArray);
            }
          }
        }

        if (!dataChanged && elapsed < FRAME_INTERVAL){
          return;
        }

        lastDrawTime = now;
        drawBackground();
        if (synthMode) drawSynth(); else drawAnalyser(currentData || prevAnalyserSnapshot);
      }

      async function start(){
        if (raf) return;
        try{
          const stream = await navigator.mediaDevices.getUserMedia({audio:true});
          audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 48000});
          analyser = audioCtx.createAnalyser();
          analyser.fftSize = 2048;
          dataArray = new Uint8Array(analyser.frequencyBinCount);
          source = audioCtx.createMediaStreamSource(stream);
          source.connect(analyser);
          synthMode = false;
          prevAnalyserSnapshot = null;
        }catch(err){
          console.warn("Mic not available; using synth waveform.", err);
          synthMode = true;
          prevAnalyserSnapshot = null;
        }
        lastDrawTime = 0;
        loop();
      }
      function stop(){
        cancelAnimationFrame(raf); raf = 0;
        prevAnalyserSnapshot = null;
        lastDrawTime = 0;
        drawBackground(); drawSynth();
        if (source){ try{ source.disconnect(); }catch{} source = null; }
        if (audioCtx){ try{ audioCtx.close(); }catch{} audioCtx = null; }
      }
      drawBackground(); drawSynth();
      return { start, stop };
    })();

    let previousConnectionState = AppState.getState().connectionState;
    AppState.subscribe((state) => {
      window.AppUI?.refresh?.();
      sidText.textContent = state.sid || '—';
      const active = state.connectionState !== 'disconnected';
      startBtn.disabled = active;
      endBtn.disabled = !active;
      if (state.latencyMs != null) {
        latencyHint.textContent = `Latency: ${Math.round(state.latencyMs)} ms`;
      } else {
        latencyHint.textContent = 'Latency: —';
      }
      if (state.connectionState === 'disconnected') {
        resetVoiceState();
        resetSuggestions();
        updateRecordingState(false, 'appstate.disconnect');
        policyCaptureLogged = false;
        if (typeof AppState?.setState === 'function') {
          AppState.setState({ policy: null });
        } else if (AppState) {
          AppState.policy = null;
        }
      } else if (state.infoFrame) {
        updateVoiceState(extractVoiceLocale(state.infoFrame));
        try {
          const framePolicy = state.infoFrame && typeof state.infoFrame === 'object'
            ? state.infoFrame.policy
            : null;
          if (framePolicy && typeof framePolicy === 'object') {
            try { AppState.policy = framePolicy; } catch {}
            if (!policyCaptureLogged) {
              // REMOVED: Old complex policy logging logic here.
              if (getLiveSocket()) {
                try {
                  flushDeferredClientLogs();
                } catch {}
              }
              policyCaptureLogged = true;
            }
          }
        } catch (err) {
          console.warn('Failed to update policy from info frame', err);
        }
      }
      const prevConnectionState = previousConnectionState;
      const nextConnectionState = state.connectionState;
      const becameConnected = prevConnectionState !== 'connected' && nextConnectionState === 'connected';
      const becameDisconnected = prevConnectionState !== 'disconnected' && nextConnectionState === 'disconnected';
      previousConnectionState = nextConnectionState;

      if (becameConnected) {
        Waveform.start();
        if (getLiveSocket()) {
          try {
            flushDeferredClientLogs();
          } catch {}
        }

        try {
          AppState?.hub?.bindSocket?.(getLiveSocket());
        } catch (err) {
          console.warn('AppState.hub.bindSocket connect failed', err);
        }
        if (audioRecorder && typeof audioRecorder.start === 'function') {
          audioRecorder.start()
            .then(() => {
              setMicPermissionGranted(true, 'recorder.start');
            })
            .catch((err) => {
              console.error('AudioRecorder start error', err);
              setMicPermissionGranted(false, 'recorder.start_error');
            });
        }
      } else if (becameDisconnected) {
        Waveform.stop();

        if (audioRecorder && typeof audioRecorder.stop === 'function') {
          try {
            audioRecorder.stop();
          } catch (err) {
            console.warn('AudioRecorder stop error', err);
          }
        }
        try {
          AppState?.hub?.bindSocket?.(null);
        } catch (err) {
          console.warn('AppState.hub.bindSocket disconnect failed', err);
        }
        updateRecordingState(false, 'appstate.disconnect');
        if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
          window.AudioPlayer.interrupt();
        }
      }
      updateResumeBanner(state);
    });

    const asrRetry = { tries: 0, timer: null };

    const SILENCE_WATCHDOG_RESUME_DELAY_MS = 350;
    let silenceWatchdogResumeTimer = null;

    function setWatchdogSuspended(suspended) {
      const next = Boolean(suspended);
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ watchdogSuspended: next });
      } else if (AppState) {
        AppState.watchdogSuspended = next;
      }
    }

    function suspendSilenceWatchdog(reason = 'tts.start') {
      clearTimeout(silenceWatchdogResumeTimer);
      silenceWatchdogResumeTimer = null;
      setWatchdogSuspended(true);
      try { AppState?.emit?.('watchdog', { action: 'suspend', reason }); } catch {}
      try { window.dispatchEvent(new CustomEvent('watchdog.suspend', { detail: { reason } })); } catch {}
    }

    function rearmSilenceWatchdogAfterDelay(
      reason = 'asr.ready',
      delayMs = SILENCE_WATCHDOG_RESUME_DELAY_MS
    ) {
      clearTimeout(silenceWatchdogResumeTimer);
      const waitMs = Number.isFinite(delayMs)
        ? Math.max(0, delayMs)
        : SILENCE_WATCHDOG_RESUME_DELAY_MS;
      silenceWatchdogResumeTimer = setTimeout(() => {
        silenceWatchdogResumeTimer = null;
        setWatchdogSuspended(false);
        try { AppState?.emit?.('watchdog', { action: 'rearm', reason }); } catch {}
        try { window.dispatchEvent(new CustomEvent('watchdog.rearm', { detail: { reason } })); } catch {}
      }, waitMs);
    }

    function updateTurnActive(active) {
      const next = Boolean(active);
      if (typeof AppState?.setState === 'function') {
        AppState.setState({ turnActive: next });
      } else if (AppState) {
        AppState.turnActive = next;
      }
      try { AppState?.emit?.('turnActive', { active: next }); } catch {}
    }

    window.addEventListener('turn.begin', () => {
      updateTurnActive(true);
    });

    window.addEventListener('turn.end', () => {
      updateTurnActive(false);
    });

window.addEventListener('tts.start', (event) => {
  const detail = event && event.detail;
  suspendSilenceWatchdog('tts.start');
  if (detail) {
    updateVoiceState(extractVoiceLocale(detail));
  }
  try {
    // Suspend silence watchdog during greet playback.
    // REMOVED: All external watchdog references
  } catch {}
  try {
    window.AppState.processing = false;
    // REMOVED: window.AppState.state.processing = false;
    window.AppUI?.refresh?.();
  } catch {}
  if (window.AudioRecorder && typeof window.AudioRecorder.handleTtsStart === 'function') {
    try {
      window.AudioRecorder.handleTtsStart();
    } catch (err) {
      console.warn('AudioRecorder handleTtsStart failed', err);
    }
  }
  window.AppUI?.refresh?.();
});

window.addEventListener('policy.snapshot', (event) => {
  const frame = event && event.detail;
  try {
    const policy = frame && typeof frame === 'object' ? frame.policy : undefined;
    const normalized = policy && typeof policy === 'object' ? policy : null;
    try { AppState.policy = normalized; } catch {}
    if (audioRecorder && typeof audioRecorder.setPolicy === 'function') {
      audioRecorder.setPolicy(normalized);
    }
  } catch (err) {
    console.warn('AudioRecorder setPolicy failed', err);
  }
});

// Turn becomes Ready → hand control to user
window.addEventListener('turn.state', (event) => {
  const frame = (event && event.detail) || {};
  const nextState = typeof frame.state === 'string'
    ? frame.state
    : (frame.meta && typeof frame.meta.state === 'string' ? frame.meta.state : null);
  const normalized = typeof nextState === 'string' ? nextState : null;
  if (typeof AppState?.setState === 'function') {
    AppState.setState({ turnState: normalized });
  } else if (AppState) {
    AppState.turnState = normalized;
  }
  window.AppUI?.refresh?.();
  if (normalized !== 'Ready') return;
});

// START REWRITE: window.addEventListener('tts.end', ...)
// Assistant TTS finished → open mic after optional delay
window.addEventListener('tts.end', () => {
  const release = () => {
    if (window.AudioRecorder && typeof window.AudioRecorder.handleTtsEnd === 'function') {
      try {
        window.AudioRecorder.handleTtsEnd();
      } catch (err) {
        console.warn('AudioRecorder handleTtsEnd failed', err);
      }
    }
    // The ASR arm request is handled by WSClient.js IMMEDIATELY on receiving the tts.end frame.
    // No action is required here except UI refresh.
    window.AppUI?.refresh?.();
  };
  release();
});
// END REWRITE: window.addEventListener('tts.end', ...)

window.addEventListener('asr.final', () => {
  try {
    window.AppState.processing = true;
    // REMOVED: window.AppState.state.processing = true;
    window.AppUI?.refresh?.();
  } catch {}
});

window.addEventListener('response', (event) => {
  try {
    const frame = event && event.detail;
    const type = typeof frame?.type === 'string' ? frame.type : '';
    if (!type || !type.startsWith('response.')) {
      return;
    }
    if (type.includes('.delta') || type.includes('.chunk') || type.endsWith('.completed')) {
      window.AppState.processing = false;
      // REMOVED: window.AppState.state.processing = false;
      window.AppUI?.refresh?.();
    }
  } catch {}
});

window.addEventListener('assistant.await_user', (event) => {
  try {
    window.AppState.processing = false;
    // REMOVED: window.AppState.state.processing = false;
    window.AppUI?.refresh?.();
  } catch {}
  window.AppUI?.refresh?.();
});

window.addEventListener('asr.unavailable', (event) => {
  if (typeof AppState?.setState === 'function') {
    AppState.setState({ asrReady: false });
  } else if (AppState) {
    AppState.asrReady = false;
  }
  const detail = event && typeof event === 'object' ? event.detail : undefined;
  let reason = null;
  if (detail && typeof detail === 'object') {
    if (typeof detail.reason === 'string') {
      reason = detail.reason;
    } else if (typeof detail.details === 'string') {
      reason = detail.details;
    } else if (typeof detail.detail === 'string') {
      reason = detail.detail;
    } else if (detail.detail && typeof detail.detail === 'object') {
      const nested = detail.detail;
      if (typeof nested.reason === 'string') {
        reason = nested.reason;
      } else if (typeof nested.details === 'string') {
        reason = nested.details;
      }
    }
  }
  if (diagHudEnabled()) {
    const reasonText = reason ? String(reason).slice(0, 120) : '';
    console.warn('diag=asr_unavailable', reasonText);
    setBadge('asr:down');
    sendDiagHudEvent(
      'EVT_CLIENT_ASR_UNAVAILABLE',
      reasonText ? { reason: reasonText } : undefined,
      {
        level: 'warn',
        badge: 'asr:down',
        message: reasonText ? `diag=asr_unavailable ${reasonText}` : 'diag=asr_unavailable',
      }
    );
  }
  window.AppUI?.refresh?.();
});

// START REWRITE: window.addEventListener('asr.ready', ...)
window.addEventListener('asr.ready', (event) => {
  // CRITICAL FIX: REMOVE ASYNCHRONOUS DEFERRAL TO BEAT SERVER TIMEOUT

  rearmSilenceWatchdogAfterDelay('asr.ready');
  if (typeof AppState?.setState === 'function') {
    AppState.setState({ asrReady: true, turnActive: true });
  } else if (AppState) {
    AppState.asrReady = true;
    AppState.turnActive = true;
  }

  // *** NEW STABLE LOGIC ***
  // Directly force the UI/state to "listening" which triggers WSClient.js to start streaming.
  updateRecordingState(true, 'asr_ready_signal');

  // REMOVED: All silence watchdog and redundant mic capture logic (arm, schedule, etc.)

  try {
    emitConsoleBusEvent('client.ui_badge', { state: 'Listening' });
  } catch {}
  if (diagHudEnabled()) {
    console.info('diag=asr_ready');
    setBadge('asr:ready');
    sendDiagHudEvent(
      'EVT_CLIENT_ASR_READY',
      event && typeof event === 'object' ? event.detail : undefined,
      { level: 'info', badge: 'asr:ready', message: 'diag=asr_ready' }
    );
  }

  // Reset any retry count for ASR
  if (asrRetry && typeof asrRetry === 'object' && asrRetry.tries > 0) {
    try {
      window.ChatView?.showSystemFromChip?.(
        "Voice is back. You can speak again when you’re ready."
      );
    } catch (err) {
      console.warn('Failed to show voice restoration message', err);
    }
    asrRetry.tries = 0;
    clearTimeout(asrRetry.timer);
    asrRetry.timer = null;
  }

  // Final UI refresh is mandatory
  window.AppUI?.refresh?.();
});
// END REWRITE: window.addEventListener('asr.ready', ...)

window.addEventListener('assistant.suggestions', (event) => {
  const detail = event && event.detail;
  applySuggestionsFrame(detail);
});


// Start mic immediately on ws.open as a safety net; send diag banner
window.addEventListener('ws.open', async () => {
  const pol = (window.AppState && window.AppState.policy) || {};
  const shouldAutoStart = pol?.audio?.start_on_ws_open === true;
  if (shouldAutoStart) {
    try {
      if (typeof window.beginWarmup === 'function') {
        window.beginWarmup(1400);
      }
    } catch (err) {
      console.warn('ws.open warmup failed', err);
    }
    try {
      const recorder = window.AudioRecorder;
      if (recorder && typeof recorder.startMicCaptureIfIdle === 'function') {
        const micReady = await recorder.startMicCaptureIfIdle();
        setMicPermissionGranted(Boolean(micReady), micReady ? 'ws.open_start' : 'ws.open_start_error');
      }
    } catch (err) {
      console.warn('AudioRecorder.startMicCaptureIfIdle on ws.open failed', err);
      setMicPermissionGranted(false, 'ws.open_start_error');
    }
    try {
      if (WSClient && typeof WSClient.startRecorderStreaming === 'function') {
        WSClient.startRecorderStreaming(pol, 'ws_open');
      }
    } catch (err) {
      console.warn('WSClient.startRecorderStreaming on ws.open failed', err);
    }
  }
  const allowBanner = window.AppState?.debug?.allow_client_banner === true;
  if (allowBanner) {
    try {
      if (WSClient && typeof WSClient.sendJSON === 'function') {
        WSClient.sendJSON({
          type: 'client.banner',
          event: { label: 'ws.open', ts_ms: Date.now() },
          info: { build: window.__BUILD_SHA__ ?? null }
        });
      } else if (WSClient && typeof WSClient.send === 'function') {
        WSClient.send({
          type: 'client.banner',
          event: { label: 'ws.open', ts_ms: Date.now() },
          info: { build: window.__BUILD_SHA__ ?? null }
        });
      }
    } catch {}
  }
});
window.addEventListener('ws.close', () => {
  resetSuggestions();
});

    // (smoke test harness removed)
  }

  init();
})();
