(() => {
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

  (function wrapDiag() {
    const ctr = (window.__WS_DIAG__ = window.__WS_DIAG__ || { types: {} });
    function maybeWrap() {
      if (!window.WSClient || typeof WSClient.send !== 'function' || WSClient.__wrapped_send__) return;
      const orig = WSClient.send.bind(WSClient);
      WSClient.send = function (frame) {
        try {
          const t = frame && frame.type;
          if (t) ctr.types[t] = (ctr.types[t] || 0) + 1;
        } catch {}
        return orig(frame);
      };
      WSClient.__wrapped_send__ = true;
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

  const VERSION_QUERY = (() => {
    if (typeof document === "undefined") {
      return "";
    }
    try {
      const current =
        document.currentScript ||
        document.querySelector('script[src*="/static/js/app.js"]') ||
        null;
      if (!current || !current.src) {
        return "";
      }
      const url = new URL(current.src, window.location.href);
      return url.search || "";
    } catch (err) {
      console.warn("Failed to derive version query", err);
      return "";
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
    const socket = wsClient.socket;
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

  function sendClientLog(label, detail) {
    const wsClient = getWsClient();
    if (!wsClientIsConnected(wsClient)) {
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

  let diagBadgeEl = null;
  let diagBadgePendingText = null;
  let diagBadgeAwaitingDom = false;

  function ensureDiagBadge() {
    if (!diagHudEnabled()) {
      return null;
    }
    if (typeof document === "undefined") {
      return null;
    }
    if (diagBadgeEl && diagBadgeEl.isConnected) {
      return diagBadgeEl;
    }
    const existing = document.getElementById("diagBadge");
    if (existing) {
      diagBadgeEl = existing;
      if (diagBadgePendingText != null) {
        existing.textContent = diagBadgePendingText;
        diagBadgePendingText = null;
      }
      return existing;
    }
    if (!document.body) {
      if (!diagBadgeAwaitingDom) {
        diagBadgeAwaitingDom = true;
        document.addEventListener("DOMContentLoaded", () => {
          diagBadgeAwaitingDom = false;
          const badge = ensureDiagBadge();
          if (badge && diagBadgePendingText != null) {
            badge.textContent = diagBadgePendingText;
            diagBadgePendingText = null;
          }
        }, { once: true });
      }
      return null;
    }
    const badge = document.createElement("div");
    badge.id = "diagBadge";
    badge.setAttribute("role", "status");
    const style = badge.style;
    style.position = "fixed";
    style.right = "12px";
    style.bottom = "12px";
    style.padding = "6px 10px";
    style.borderRadius = "10px";
    style.background = "rgba(17,24,39,0.78)";
    style.color = "#f8fafc";
    style.font = "12px/1.4 system-ui, -apple-system, \"Segoe UI\", sans-serif";
    style.zIndex = "5000";
    style.pointerEvents = "none";
    style.boxShadow = "0 6px 24px rgba(15, 23, 42, 0.25)";
    badge.textContent = diagBadgePendingText != null ? diagBadgePendingText : "diag";
    diagBadgePendingText = null;
    document.body.appendChild(badge);
    diagBadgeEl = badge;
    return badge;
  }

  function setBadge(text) {
    if (!diagHudEnabled()) {
      return;
    }
    const value = text == null ? "" : String(text);
    const badge = ensureDiagBadge();
    if (badge) {
      badge.textContent = value;
      diagBadgePendingText = null;
    } else {
      diagBadgePendingText = value;
    }
  }

  async function getMicStream() {
    if (!navigator || !navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      throw new Error("mediaDevices.getUserMedia unavailable");
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (diagHudEnabled()) {
      const tracks = typeof stream.getAudioTracks === "function"
        ? stream.getAudioTracks().map((track) => ({ label: track.label, enabled: track.enabled }))
        : [];
      console.info("DIAG_MEDIA_OK", tracks);
      setBadge("mic:ok");
      sendDiagHudEvent("EVT_CLIENT_MEDIA_OK", { tracks }, { level: "info", badge: "mic:ok", message: "Media stream ready" });
    }
    return stream;
  }

  function attachRecorder(stream, onChunk) {
    if (!stream) {
      throw new Error("MediaStream required");
    }
    if (typeof MediaRecorder === "undefined") {
      throw new Error("MediaRecorder unsupported");
    }
    const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
    let sentBytes = 0;
    let chunkCount = 0;
    const sampleEvery = Math.max(1, diagChunkSampleN());
    recorder.addEventListener("start", () => {
      if (typeof logClient === 'function') {
        logClient('client.mic', 'evt=recorder_started');
      }
      if (diagHudEnabled()) {
        const ts = Date.now();
        console.info("DIAG_RECORDER_STARTED", ts);
        const badge = "rec:start";
        setBadge(badge);
        sendDiagHudEvent(
          "EVT_CLIENT_RECORDER_STARTED",
          { timestamp: ts },
          { level: "info", badge, message: "Recorder started" }
        );
      }
    });
    recorder.addEventListener("dataavailable", (event) => {
      const blob = event && event.data;
      if (!blob || !blob.size) {
        return;
      }
      chunkCount += 1;
      const bytes = blob.size;
      const shouldSample = chunkCount % sampleEvery === 0;
      if (typeof logClient === 'function') {
        logClient('client.mic', `evt=mic_chunk bytes=${bytes}`);
      }
      if (diagHudEnabled() && shouldSample) {
        console.debug("DIAG_CHUNK", { size: bytes, chunkCount });
        const badge = `chunks:${chunkCount}`;
        setBadge(badge);
        sendDiagHudEvent(
          "EVT_CLIENT_CHUNK_SAMPLE",
          { size: bytes, chunkCount },
          { level: "debug", badge, sample: true, message: "Sampled recorder chunk" }
        );
      }
      let sendError = null;
      try {
        const maybeSend = sendAudioChunk(blob);
        if (maybeSend && typeof maybeSend.then === 'function') {
          maybeSend.catch((err) => {
            sendError = err;
            if (diagHudEnabled()) {
              console.warn("DIAG_CHUNK_SEND_ERROR", err);
              sendDiagHudEvent("EVT_CLIENT_CHUNK_ERROR", { message: err && err.message }, {
                level: "warn",
                message: "Failed to send chunk"
              });
            }
          });
        }
      } catch (err) {
        sendError = err;
        if (diagHudEnabled()) {
          console.warn("DIAG_CHUNK_SEND_ERROR", err);
          sendDiagHudEvent("EVT_CLIENT_CHUNK_ERROR", { message: err && err.message }, {
            level: "warn",
            message: "Failed to send chunk"
          });
        }
      }
      if (typeof onChunk === 'function') {
        try {
          const result = onChunk(blob);
          if (result && typeof result.then === 'function') {
            result.catch((err) => {
              if (diagHudEnabled()) {
                console.warn("DIAG_CHUNK_HANDLER_ERROR", err);
                sendDiagHudEvent("EVT_CLIENT_CHUNK_ERROR", { message: err && err.message }, {
                  level: "warn",
                  message: "Chunk handler rejected"
                });
              }
            });
          }
        } catch (err) {
          if (diagHudEnabled()) {
            console.warn("DIAG_CHUNK_HANDLER_ERROR", err);
            sendDiagHudEvent("EVT_CLIENT_CHUNK_ERROR", { message: err && err.message }, {
              level: "warn",
              message: "Chunk handler failed"
            });
          }
        }
      }
      sentBytes += bytes;
      if (!sendError && diagHudEnabled() && shouldSample) {
        console.debug("DIAG_WS_BIN_SENT", { chunkCount, sentBytes });
        sendDiagHudEvent(
          "EVT_CLIENT_WS_BINARY_SENT",
          { chunkCount, sentBytes },
          { level: "debug", sample: true, message: "Sampled binary chunk sent" }
        );
      }
    });
    return recorder;
  }

  function sendAudioChunk(chunkBlobOrBuf) {
    if (!chunkBlobOrBuf) {
      return null;
    }
    const client = window.WSClient;
    const bytes = chunkBlobOrBuf.byteLength || chunkBlobOrBuf.size || 0;
    let result = null;
    if (client && typeof client.send === 'function') {
      try {
        result = client.send(chunkBlobOrBuf, { binary: true });
      } catch (err) {
        console.error('WSClient audio chunk send error', err);
      }
    } else if (client && typeof client.sendBinary === 'function') {
      try {
        result = client.sendBinary(chunkBlobOrBuf, { lane: 'mic' });
      } catch (err) {
        console.error('WSClient audio chunk legacy send error', err);
      }
    }
    if (typeof logClient === 'function') {
      logClient('client.mic', `evt=mic_chunk_sent bytes=${bytes}`);
    }
    return result;
  }

  const DiagRecorder = (() => {
    let stream = null;
    let recorder = null;
    let startPromise = null;

    function cleanupStream() {
      if (!stream) {
        return;
      }
      try {
        const tracks = typeof stream.getTracks === "function" ? stream.getTracks() : [];
        tracks.forEach((track) => {
          try {
            track.stop();
          } catch (err) {
            if (diagHudEnabled()) {
              console.warn("DIAG_TRACK_STOP_ERROR", err);
              sendDiagHudEvent("EVT_CLIENT_TRACK_STOP_ERROR", { message: err && err.message }, {
                level: "warn",
                message: "Failed to stop track"
              });
            }
          }
        });
      } finally {
        stream = null;
      }
    }

    function stop(reason) {
      if (recorder) {
        try {
          if (recorder.state !== "inactive") {
            recorder.stop();
          }
        } catch (err) {
          if (diagHudEnabled()) {
            console.warn("DIAG_RECORDER_STOP_ERROR", err);
            sendDiagHudEvent("EVT_CLIENT_RECORDER_STOP_ERROR", { message: err && err.message }, {
              level: "warn",
              message: "Recorder stop error"
            });
          }
        }
        recorder = null;
      }
      cleanupStream();
      if (diagHudEnabled()) {
        if (reason) {
          setBadge(`stop:${reason}`);
          sendDiagHudEvent("EVT_CLIENT_RECORDER_STOP", { reason }, {
            level: "info",
            badge: `stop:${reason}`,
            message: "Recorder stopped"
          });
        } else {
          setBadge("stop");
          sendDiagHudEvent("EVT_CLIENT_RECORDER_STOP", null, {
            level: "info",
            badge: "stop",
            message: "Recorder stopped"
          });
        }
      }
    }

    async function startInternal(trigger) {
      if (!diagHudEnabled()) {
        return null;
      }
      if (recorder && recorder.state !== "inactive") {
        return recorder;
      }
      const wsClient = window.WSClient;
      const ws = wsClient && wsClient.socket;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return null;
      }
      try {
        stream = await getMicStream();
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_MEDIA_ERROR", err);
          setBadge("mic:fail");
          sendDiagHudEvent("EVT_CLIENT_MEDIA_ERROR", { message: err && err.message }, {
            level: "warn",
            badge: "mic:fail",
            message: "Failed to access microphone"
          });
        }
        stream = null;
        return null;
      }
      try {
        recorder = attachRecorder(stream);
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_RECORDER_ERROR", err);
          setBadge("rec:fail");
          sendDiagHudEvent("EVT_CLIENT_RECORDER_ERROR", { message: err && err.message }, {
            level: "warn",
            badge: "rec:fail",
            message: "Recorder initialization failed"
          });
        }
        cleanupStream();
        recorder = null;
        return null;
      }
      recorder.addEventListener("stop", () => {
        if (diagHudEnabled()) {
          setBadge("rec:stop");
          sendDiagHudEvent("EVT_CLIENT_RECORDER_STOP", null, {
            level: "info",
            badge: "rec:stop",
            message: "Recorder stopped"
          });
        }
      });
      const cap = (runtimeState && runtimeState.policy && runtimeState.policy.capture) || {};
      const sliceCandidate = Number(cap && cap.timeslice_ms);
      const slice = Number.isFinite(sliceCandidate) && sliceCandidate > 0 ? sliceCandidate : 250;
      if (typeof logClient === 'function') {
        logClient('client.mic', `evt=recorder_start slice_ms=${slice}`);
      }
      try {
        recorder.start(slice);
      } catch (err) {
        if (diagHudEnabled()) {
          console.warn("DIAG_RECORDER_START_ERROR", err);
          setBadge("rec:fail");
          sendDiagHudEvent("EVT_CLIENT_RECORDER_START_ERROR", { message: err && err.message }, {
            level: "warn",
            badge: "rec:fail",
            message: "Recorder start error"
          });
        }
        stop("start_err");
        return null;
      }
      if (diagHudEnabled()) {
        setBadge(trigger ? `rec:${trigger}` : "rec");
        sendDiagHudEvent("EVT_CLIENT_RECORDER_ACTIVE", { trigger }, {
          level: "info",
          badge: trigger ? `rec:${trigger}` : "rec",
          message: "Recorder active"
        });
      }
      return recorder;
    }

    function maybeStart(trigger) {
      if (!diagHudEnabled()) {
        return;
      }
      if (recorder && recorder.state !== "inactive") {
        return;
      }
      if (startPromise) {
        return;
      }
      const wsClient = window.WSClient;
      const ws = wsClient && wsClient.socket;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return;
      }
      const promise = startInternal(trigger);
      if (!promise) {
        return;
      }
      startPromise = promise;
      promise.finally(() => {
        if (startPromise === promise) {
          startPromise = null;
        }
      }).catch(() => {
        if (diagHudEnabled()) {
          console.warn("DIAG_START_REJECTED");
          sendDiagHudEvent("EVT_CLIENT_RECORDER_START_REJECTED", null, {
            level: "warn",
            message: "Recorder start promise rejected"
          });
        }
      });
    }

    return {
      maybeStart,
      stop,
      hudEnabled: diagHudEnabled
    };
  })();

  async function ensureRuntimeModules() {
    if (!window.AppState) {
      await loadScript("state.js");
    }
    if (!window.AudioPlayer) {
      await loadScript("audio_player.js");
    }
    if (!window.WSClient) {
      await loadScript("ws_client.js");
    }
    if (!window.AudioRecorder) {
      await loadScript("audio_recorder.js");
    }
    if (!window.PolicyBadges) {
      await loadScript("policy_badges.js");
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

    const runtimeState = {
      policy: null,
      turnState: null,
      asrReady: false,
      isRecording: false,
      micPermissionGranted: false,
      audioPlaybackIdle: true,
      policyCaptureLogged: false,
    };

    const micSessionTelemetry = {
      micOpenLogged: false,
      firstChunkLogged: false,
      recBadgeLogged: false,
    };
    let micPermissionTelemetryLogged = false;

    const CLIENT_MIC_OPEN_EVENT = 'EVT_CLIENT_MIC_OPEN';
    let pendingClientReady = null;
    let clientReadyTimerId = null;
    let clientReadyStats = null;
    const deferredClientLogs = [];

    function startClientReadyTracking(detail) {
      clientReadyStats = {
        detail,
        attempts: 0,
        events: [],
        startedAt: Date.now(),
      };
      recordClientReadyEvent('enqueue', {
        vendor: detail && detail.vendor ? detail.vendor : undefined,
        micTs: detail && typeof detail.ts === 'number' ? detail.ts : undefined,
      });
    }

    function recordClientReadyEvent(kind, meta) {
      if (!clientReadyStats) {
        return;
      }
      const entry = {
        kind: typeof kind === 'string' && kind ? kind.slice(0, 32) : 'event',
        ts: Date.now(),
      };
      if (meta && typeof meta === 'object') {
        const sanitized = {};
        const keys = Object.keys(meta);
        for (let i = 0; i < keys.length && i < 8; i += 1) {
          const key = keys[i];
          if (!key) continue;
          const value = meta[key];
          if (value === undefined || value === null) continue;
          const shortKey = key.slice(0, 32);
          if (typeof value === 'number') {
            if (Number.isFinite(value)) {
              sanitized[shortKey] = value;
            }
          } else if (typeof value === 'string') {
            sanitized[shortKey] = value.slice(0, 96);
          } else if (typeof value === 'boolean') {
            sanitized[shortKey] = value;
          }
        }
        if (Object.keys(sanitized).length) {
          entry.meta = sanitized;
        }
      }
      clientReadyStats.events.push(entry);
      if (clientReadyStats.events.length > 12) {
        clientReadyStats.events.splice(0, clientReadyStats.events.length - 12);
      }
    }

    function enqueueDeferredClientLog(label, detail) {
      deferredClientLogs.push({ label, detail });
      if (deferredClientLogs.length > 20) {
        deferredClientLogs.splice(0, deferredClientLogs.length - 20);
      }
    }

    function flushDeferredClientLogs() {
      if (!deferredClientLogs.length) {
        return;
      }
      let remaining = deferredClientLogs.length;
      while (remaining > 0 && deferredClientLogs.length) {
        const entry = deferredClientLogs.shift();
        remaining -= 1;
        if (!entry) {
          continue;
        }
        if (!sendClientLog(entry.label, entry.detail)) {
          deferredClientLogs.unshift(entry);
          break;
        }
      }
    }

    function safeErr(err) {
      if (err == null) {
        return 'unknown';
      }
      if (typeof err === 'string') {
        return err.slice(0, 160);
      }
      if (typeof err === 'object' && typeof err.message === 'string' && err.message) {
        return err.message.slice(0, 160);
      }
      try {
        const serialized = JSON.stringify(err);
        if (typeof serialized === 'string' && serialized) {
          return serialized.slice(0, 160);
        }
      } catch (_) {}
      try {
        return String(err).slice(0, 160);
      } catch (_) {
        return 'error';
      }
    }

    function logClient(message, detail) {
      let text = '';
      let payloadSource = detail;
      if (typeof message === 'string' && message === 'client.mic' && typeof detail === 'string') {
        text = `${message} ${detail}`;
        payloadSource = { message: text };
      } else {
        text = typeof message === 'string'
          ? message
          : (message == null ? '' : String(message));
        if (typeof detail === 'string' && detail) {
          payloadSource = { message: detail };
        }
      }
      const payload = payloadSource && typeof payloadSource === 'object' ? { ...payloadSource } : {};
      if (text) {
        try {
          console.log(text);
        } catch (_) {}
        if (!payload.message) {
          payload.message = text.slice(0, 256);
        }
      }
      if (!sendClientLog('client.mic', payload)) {
        enqueueDeferredClientLog('client.mic', payload);
      }
    }

    function resetMicSessionTelemetry() {
      micSessionTelemetry.micOpenLogged = false;
      micSessionTelemetry.firstChunkLogged = false;
      micSessionTelemetry.recBadgeLogged = false;
    }

    function noteRecordingBadgeOn(source) {
      if (micSessionTelemetry.recBadgeLogged) {
        return;
      }
      micSessionTelemetry.recBadgeLogged = true;
      const detail = { event: 'ui_rec_badge_on' };
      if (source) {
        detail.source = String(source).slice(0, 32);
      }
      logClient('evt=ui_rec_badge_on', detail);
    }

    function updateRecordingState(active, source) {
      const previous = runtimeState.isRecording;
      const next = !!active;
      runtimeState.isRecording = next;
      if (next) {
        if (!previous) {
          resetMicSessionTelemetry();
          noteRecordingBadgeOn(source);
        }
      } else if (previous) {
        resetMicSessionTelemetry();
      }
      return next;
    }

    function noteMicOpen(source) {
      if (micSessionTelemetry.micOpenLogged) {
        return;
      }
      micSessionTelemetry.micOpenLogged = true;
      const detail = { event: 'mic_open' };
      if (source) {
        detail.source = String(source).slice(0, 32);
      }
      logClient('evt=mic_open', detail);
    }

    function setMicPermissionGranted(granted, source) {
      const next = !!granted;
      runtimeState.micPermissionGranted = next;
      if (next) {
        if (!micPermissionTelemetryLogged) {
          micPermissionTelemetryLogged = true;
          const detail = { event: 'mic_perm_granted' };
          if (source) {
            detail.source = String(source).slice(0, 32);
          }
          logClient('evt=mic_perm_granted', detail);
        }
      } else {
        micPermissionTelemetryLogged = false;
      }
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

    if (audioRecorder && typeof audioRecorder._onWebmData === 'function') {
      const originalOnWebmData = audioRecorder._onWebmData.bind(audioRecorder);
      audioRecorder._onWebmData = function patchedOnWebmData(event) {
        if (!micSessionTelemetry.firstChunkLogged) {
          const size = event && event.data && typeof event.data.size === 'number'
            ? event.data.size
            : 0;
          if (size > 0) {
            micSessionTelemetry.firstChunkLogged = true;
            const detail = { event: 'mic_first_chunk', bytes: size };
            logClient(`evt=mic_first_chunk bytes=${size}`, detail);
          }
        }
        return originalOnWebmData(event);
      };
    }

    function logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, extra) {
      const cap = (runtimeState && runtimeState.policy && runtimeState.policy.capture) || {};
      const gateSnapshot = {
        asrReady: !!runtimeState.asrReady,
        turnState: runtimeState.turnState || '',
        micPerm: !!runtimeState.micPermissionGranted,
        start_on_asr_ready: !!cap.start_on_asr_ready,
        start_on_turn_ready: !!cap.start_on_turn_ready
      };
      if (typeof logClient === 'function') {
        try {
          const gatesJson = JSON.stringify(gateSnapshot);
          logClient('client.mic', `evt=maybe_autostart_blocked gates=${gatesJson} trigger=${triggerLabel} reason=${reasonLabel}`);
        } catch (err) {
          logClient('client.mic', `evt=maybe_autostart_blocked gates_error trigger=${triggerLabel} reason=${reasonLabel}`);
        }
      }
      const parts = [
        `asrReady=${gates.asrReady}`,
        `turnState=${gates.turnState === null ? 'null' : gates.turnState}`,
        `micPerm=${gates.micPerm}`,
        `recording=${gates.recording}`,
      ];
      const detail = {
        event: 'maybe_autostart_blocked',
        trigger: triggerLabel,
        reason: reasonLabel,
        gates: {
          asrReady: gates.asrReady,
          turnState: gates.turnState,
          micPerm: gates.micPerm,
          recording: gates.recording,
        },
      };
      if (extra && typeof extra === 'object') {
        const keys = Object.keys(extra);
        if (keys.length) {
          detail.extra = {};
          for (let i = 0; i < keys.length && i < 8; i += 1) {
            const key = keys[i];
            if (!key) continue;
            detail.extra[key.slice(0, 32)] = extra[key];
          }
        }
      }
      logClient(`evt=maybe_autostart_blocked gates={${parts.join(' ')}}`, detail);
    }

    function maybeAutoStartCapture(trigger, reason) {
      const capturePolicy = (runtimeState.policy && runtimeState.policy.capture) || {};
      const wantsAsrReady = Boolean(capturePolicy.start_on_asr_ready);
      const wantsTurnReady = Boolean(capturePolicy.start_on_turn_ready);
      if (!(wantsAsrReady || wantsTurnReady)) {
        const triggerLabel = trigger ? String(trigger).slice(0, 32) : 'unknown';
        const reasonLabel = reason ? String(reason).slice(0, 32) : 'unknown';
        const rawTurnState = typeof runtimeState.turnState === 'string' && runtimeState.turnState
          ? runtimeState.turnState.slice(0, 32)
          : null;
        const gates = {
          asrReady: Boolean(runtimeState.asrReady),
          turnState: rawTurnState,
          micPerm: Boolean(runtimeState.micPermissionGranted),
          recording: Boolean(runtimeState.isRecording),
        };
        logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, { blockedBy: 'policy_disabled' });
        return;
      }

      const triggerLabel = trigger ? String(trigger).slice(0, 32) : 'unknown';
      const reasonLabel = reason ? String(reason).slice(0, 32) : 'unknown';
      const rawTurnState = typeof runtimeState.turnState === 'string' && runtimeState.turnState
        ? runtimeState.turnState.slice(0, 32)
        : null;
      const gates = {
        asrReady: Boolean(runtimeState.asrReady),
        turnState: rawTurnState,
        micPerm: Boolean(runtimeState.micPermissionGranted),
        recording: Boolean(runtimeState.isRecording),
      };

      if (runtimeState.isRecording) {
        logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, {
          blockedBy: 'recording',
          audioIdle: runtimeState.audioPlaybackIdle,
        });
        return;
      }
      if (!runtimeState.micPermissionGranted) {
        logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, {
          blockedBy: 'mic_permission',
          audioIdle: runtimeState.audioPlaybackIdle,
        });
        return;
      }
      if (!runtimeState.asrReady) {
        logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, {
          blockedBy: 'asr_ready',
          audioIdle: runtimeState.audioPlaybackIdle,
        });
        return;
      }
      if (wantsTurnReady && !runtimeState.audioPlaybackIdle) {
        logMaybeAutostartBlocked(triggerLabel, reasonLabel, gates, {
          blockedBy: 'audio_playback',
          audioIdle: runtimeState.audioPlaybackIdle,
        });
        return;
      }

      logClient(`evt=mic_arm trigger=${triggerLabel} reason=${reasonLabel}`, {
        event: 'mic_arm',
        trigger: triggerLabel,
        reason: reasonLabel,
      });
      startMicCapture()
        .then((started) => {
          if (!started) {
            logClient('evt=mic_open_failed err=mic_not_started', {
              event: 'mic_open_failed',
              error: 'mic_not_started',
            });
            return;
          }
          updateRecordingState(true, 'auto_start_capture');
          noteMicOpen('auto_start_capture');
        })
        .catch((err) => {
          const errText = safeErr(err);
          logClient(`evt=mic_open_failed err=${errText}`, {
            event: 'mic_open_failed',
            error: errText,
          });
        });
    }

    function maybeLogClientReadyOutcome(outcome, extraMeta) {
      if (!clientReadyStats) {
        return;
      }
      const summary = {
        outcome: typeof outcome === 'string' && outcome ? outcome.slice(0, 32) : 'unknown',
        attempts: clientReadyStats.attempts,
        startedMs: clientReadyStats.startedAt,
      };
      if (typeof clientReadyStats.startedAt === 'number') {
        const elapsed = Date.now() - clientReadyStats.startedAt;
        if (Number.isFinite(elapsed)) {
          summary.durationMs = Math.max(0, elapsed);
        }
      }
      if (clientReadyStats.detail && typeof clientReadyStats.detail.ts === 'number') {
        summary.micTs = clientReadyStats.detail.ts;
      }
      if (clientReadyStats.detail && clientReadyStats.detail.vendor) {
        summary.vendor = clientReadyStats.detail.vendor.slice(0, 64);
      }
      if (clientReadyStats.events.length) {
        summary.events = clientReadyStats.events.slice(-12);
      }
      const extraDetail = cloneDiagDetail(extraMeta);
      if (extraDetail !== undefined) {
        summary.extra = extraDetail;
      }
      const wsClient = getWsClient();
      const socket = wsClient && wsClient.socket;
      if (socket && typeof socket.readyState === 'number') {
        summary.readyState = socket.readyState;
      }
      if (!sendClientLog('client_ready_handshake', summary)) {
        enqueueDeferredClientLog('client_ready_handshake', summary);
      } else {
        flushDeferredClientLogs();
      }
      clientReadyStats = null;
    }

    function normalizeMicOpenDetail(detail) {
      const rawTs = detail ? detail.ts : null;
      const tsNumber = Number(rawTs);
      const tsValue = Number.isFinite(tsNumber) ? tsNumber : Date.now();
      const vendorValue = detail && typeof detail.vendor === 'string' ? detail.vendor.trim() : '';
      const vendor = vendorValue ? vendorValue.slice(0, 64) : null;
      return { ts: tsValue, vendor };
    }

    function trySendClientReady(detail) {
      if (!detail) return false;
      const wsClient = getWsClient();
      if (!wsClient || typeof wsClient.send !== 'function') {
        recordClientReadyEvent('wsclient_missing');
        return false;
      }
      const socket = wsClient.socket;
      const readyState = socket && typeof socket.readyState === 'number' ? socket.readyState : -1;
      if (clientReadyStats) {
        clientReadyStats.attempts += 1;
        recordClientReadyEvent('attempt', { readyState });
      }
      if (!socket || readyState !== WebSocket.OPEN) {
        recordClientReadyEvent('socket_not_open', { readyState });
        return false;
      }
      const payload = {
        type: 'client.ready',
        mic: {
          state: 'open',
          ts: detail.ts
        }
      };
      if (detail.vendor) {
        payload.mic.vendor = detail.vendor;
      }
      try {
        wsClient.send(payload);
        recordClientReadyEvent('sent', { readyState });
        return true;
      } catch (err) {
        recordClientReadyEvent('send_error', {
          readyState,
          message: err && err.message ? String(err.message).slice(0, 120) : 'send_failed'
        });
        console.warn('client.ready send failed', err);
        return false;
      }
    }

    function flushPendingClientReady() {
      if (!pendingClientReady) {
        clientReadyTimerId = null;
        return;
      }
      if (trySendClientReady(pendingClientReady)) {
        pendingClientReady = null;
        clientReadyTimerId = null;
        maybeLogClientReadyOutcome('sent');
        return;
      }
      recordClientReadyEvent('retry_wait', { delayMs: 200 });
      clientReadyTimerId = window.setTimeout(flushPendingClientReady, 200);
    }

    function enqueueClientReady(detail) {
      const normalized = normalizeMicOpenDetail(detail);
      if (!normalized) {
        return;
      }
      const detailChanged =
        !!clientReadyStats &&
        (clientReadyStats.detail.ts !== normalized.ts || clientReadyStats.detail.vendor !== normalized.vendor);
      if (detailChanged) {
        const replacementMeta = {
          reason: 'detail_changed',
          prevTs: clientReadyStats.detail && typeof clientReadyStats.detail.ts === 'number'
            ? clientReadyStats.detail.ts
            : undefined,
          prevVendor: clientReadyStats.detail && clientReadyStats.detail.vendor
            ? clientReadyStats.detail.vendor.slice(0, 64)
            : undefined,
        };
        recordClientReadyEvent('detail_replaced', replacementMeta);
        maybeLogClientReadyOutcome('replaced', replacementMeta);
      }
      pendingClientReady = normalized;
      if (!clientReadyStats) {
        startClientReadyTracking(normalized);
      } else {
        recordClientReadyEvent('enqueue_duplicate');
      }
      if (clientReadyTimerId !== null) {
        return;
      }
      flushPendingClientReady();
    }

    window.addEventListener(CLIENT_MIC_OPEN_EVENT, (event) => {
      setMicPermissionGranted(true, 'mic_open_event');
      updateRecordingState(true, 'mic_open_event');
      noteMicOpen('mic_open_event');
      enqueueClientReady(event && event.detail);
    });

    window.addEventListener('input.start', () => {
      updateRecordingState(true, 'input.start');
      setMicPermissionGranted(true, 'input.start');
    });

    window.addEventListener('input.stop', () => {
      updateRecordingState(false, 'input.stop');
    });

    window.addEventListener('ws.close', () => {
      if (clientReadyStats && pendingClientReady) {
        recordClientReadyEvent('socket_closed', { reason: 'ws.close' });
        maybeLogClientReadyOutcome('socket_closed', { reason: 'ws.close' });
      }
      pendingClientReady = null;
      if (clientReadyTimerId !== null) {
        clearTimeout(clientReadyTimerId);
        clientReadyTimerId = null;
      }
      runtimeState.asrReady = false;
      runtimeState.turnState = null;
      updateRecordingState(false, 'ws.close');
      setMicPermissionGranted(false, 'ws.close');
      runtimeState.policy = null;
    });

    window.addEventListener('ws.open', () => {
      runtimeState.asrReady = false;
      runtimeState.turnState = null;
      updateRecordingState(false, 'ws.open');
      runtimeState.policy = null;
      flushDeferredClientLogs();
    });
    
    if (window.PolicyBadges && typeof window.PolicyBadges.init === "function") {
      try {
        window.PolicyBadges.init();
      } catch (err) {
        console.warn("Failed to initialize PolicyBadges", err);
      }
    }

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
    const connLabel = document.getElementById('connLabel');
    const statusDot = document.querySelector('.status-dot');
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
          WSClient && WSClient.send && WSClient.send({ type: 'chat.user', text });
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
        const audioPlayer = window.AudioPlayer;
        if (audioPlayer && typeof audioPlayer.interrupt === 'function') {
          audioPlayer.interrupt();
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
      const wsUrl = `/ws/v2/chat?${params.toString()}`;

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
      const ctx = canvas.getContext('2d', {alpha:false});
      let raf = 0, analyser = null, source = null, audioCtx = null, dataArray = null;
      let synthMode = true, t = 0;

      function resizeCanvas(){
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        canvas.width = canvas.clientWidth * dpr;
        canvas.height = canvas.clientHeight * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      window.addEventListener('resize', resizeCanvas, {passive:true});
      resizeCanvas();

      function drawBackground(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const g = ctx.createLinearGradient(0,0,w,h);
        g.addColorStop(0,'#0b1222'); g.addColorStop(1,'#0a0f1b');
        ctx.fillStyle = g; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 1;
        const gap = 24;
        ctx.beginPath();
        for(let x=0;x<w;x+=gap){ ctx.moveTo(x,0); ctx.lineTo(x,h); }
        for(let y=0;y<h;y+=gap){ ctx.moveTo(0,y); ctx.lineTo(w,y); }
        ctx.stroke();        
      }

      function drawSynth(){
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

      function drawAnalyser(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!analyser || !dataArray){ drawSynth(); return; }
        analyser.getByteFrequencyData(dataArray);
        const bars = 96;
        const step = Math.max(1, Math.floor(dataArray.length / bars));
        ctx.save();
        ctx.translate(0, h/2);
        for(let i=0;i<bars;i++){
          const v = dataArray[i*step] / 255;
          const y = (v*v) * (h*0.35);
          const k = i/(bars-1);
          ctx.strokeStyle = `hsla(${22*(1-k) + 204*k}, 85%, ${45 + v*20}%, .9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), y); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), -y*0.6); ctx.stroke();
        }
        ctx.restore();
      }

      function loop(){
        drawBackground();
        if (synthMode) drawSynth(); else drawAnalyser();
        raf = requestAnimationFrame(loop);
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
        }catch(err){
          console.warn("Mic not available; using synth waveform.", err);
          synthMode = true;
        }
        loop();
      }
      function stop(){
        cancelAnimationFrame(raf); raf = 0;
        drawBackground(); drawSynth();
        if (source){ try{ source.disconnect(); }catch{} source = null; }
        if (audioCtx){ try{ audioCtx.close(); }catch{} audioCtx = null; }
      }
      drawBackground(); drawSynth();
      return { start, stop };
    })();

    let previousConnectionState = AppState.getState().connectionState;
    AppState.subscribe((state) => {
      sidText.textContent = state.sid || '—';
      let label = 'Disconnected';
      if (state.connectionState === 'connected') label = 'Connected';
      else if (state.connectionState === 'connecting') label = 'Connecting…';
      else if (state.connectionState === 'resuming') label = 'Resuming…';
      connLabel.textContent = label;
      const active = state.connectionState !== 'disconnected';
      statusDot.classList.toggle('on', active);
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
        runtimeState.turnState = null;
        runtimeState.asrReady = false;
        runtimeState.policy = null;
        runtimeState.audioPlaybackIdle = true;
        runtimeState.policyCaptureLogged = false;
      } else if (state.infoFrame) {
        updateVoiceState(extractVoiceLocale(state.infoFrame));
        try {
          const framePolicy = state.infoFrame && typeof state.infoFrame === 'object'
            ? state.infoFrame.policy
            : null;
          if (framePolicy && typeof framePolicy === 'object') {
            runtimeState.policy = framePolicy;
            if (!runtimeState.policyCaptureLogged) {
              const cap = framePolicy && typeof framePolicy.capture === 'object' ? framePolicy.capture : {};
              const asrInput = cap && typeof cap.asr_input === 'string' ? cap.asr_input : '';
              const timeslice = cap && cap.timeslice_ms !== undefined ? cap.timeslice_ms : '';
              if (typeof logClient === 'function') {
                logClient('client.mic', `evt=policy_capture asr_input=${asrInput} timeslice_ms=${timeslice}`);
              }
              if (typeof flushDeferredClientLogs === 'function') {
                flushDeferredClientLogs();
              }
              runtimeState.policyCaptureLogged = true;
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
        flushDeferredClientLogs();

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
        if (pendingClientReady && clientReadyTimerId === null) {
          flushPendingClientReady();
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
        updateRecordingState(false, 'appstate.disconnect');
        if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
          window.AudioPlayer.interrupt();
        }
      }
      updateResumeBanner(state);
    });

    const POST_TTS_RELEASE_MS = 150;

    let asrRetry = { tries: 0, timer: null };

    function scheduleAsrRearm() {
      if (typeof window === 'undefined') {
        return;
      }
      if (asrRetry && typeof asrRetry === 'object') {
        clearTimeout(asrRetry.timer);
      }
      const tries = asrRetry && typeof asrRetry.tries === 'number' ? asrRetry.tries : 0;
      const exponent = Math.min(5, tries);
      const delay = Math.min(30000, 1000 * Math.pow(2, exponent));
      if (asrRetry && typeof asrRetry === 'object') {
        asrRetry.tries = tries + 1;
      }
      const timer = setTimeout(() => {
        try {
          const payload = { type: 'asr.rearm.request' };
          const client = window.WSClient;
          if (client && typeof client.send === 'function') {
            client.send(payload);
            return;
          }
          const ws = window.WS;
          if (ws && typeof ws.send === 'function') {
            ws.send(payload);
          }
        } catch (err) {
          console.warn('Failed to send asr.rearm.request', err);
        }
      }, delay);
      if (asrRetry && typeof asrRetry === 'object') {
        asrRetry.timer = timer;
      }
    }

window.addEventListener('tts.start', (event) => {
  const detail = event && event.detail;
  if (detail) {
    updateVoiceState(extractVoiceLocale(detail));
  }
  runtimeState.audioPlaybackIdle = false;
  if (window.AudioRecorder && typeof window.AudioRecorder.handleTtsStart === 'function') {
    try {
      window.AudioRecorder.handleTtsStart();
    } catch (err) {
      console.warn('AudioRecorder handleTtsStart failed', err);
    }
  }
});

window.addEventListener('policy.snapshot', (event) => {
  const frame = event && event.detail;
  try {
    const policy = frame && typeof frame === 'object' ? frame.policy : undefined;
    runtimeState.policy = policy && typeof policy === 'object' ? policy : null;
    if (audioRecorder && typeof audioRecorder.setPolicy === 'function') {
      audioRecorder.setPolicy(policy);
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
  runtimeState.turnState = typeof nextState === 'string' ? nextState : null;
  if (runtimeState.turnState !== 'Ready') return;

  // Optional: why we became ready (e.g., 'tts')
  const reason = typeof frame.reason === 'string'
    ? frame.reason
    : (frame.meta && typeof frame.meta.reason === 'string' ? frame.meta.reason : null);
  void reason; // not strictly needed, but kept for clarity

  runtimeState.audioPlaybackIdle = true;
  DiagRecorder.maybeStart('turn');
  maybeAutoStartCapture('turn_ready', 'tts_end');

  console.info('ui: turn=Ready → startMicCaptureIfIdle()');
  if (window.AudioRecorder && typeof window.AudioRecorder.startMicCaptureIfIdle === 'function') {
    try {
      const maybe = window.AudioRecorder.startMicCaptureIfIdle();
      if (maybe && typeof maybe.then === 'function' && typeof maybe.catch === 'function') {
        maybe.catch((err) => {
          console.error('AudioRecorder startMicCaptureIfIdle on turn=Ready failed', err);
        });
      }
    } catch (err) {
      console.error('AudioRecorder startMicCaptureIfIdle on turn=Ready threw', err);
    }
  }
});

// Assistant TTS finished → open mic after optional delay
window.addEventListener('tts.end', () => {
  const release = () => {
    DiagRecorder.maybeStart('ready');
    if (window.AudioRecorder && typeof window.AudioRecorder.handleTtsEnd === 'function') {
      try {
        window.AudioRecorder.handleTtsEnd();
      } catch (err) {
        console.warn('AudioRecorder handleTtsEnd failed', err);
      }
    }
  };

  if (typeof POST_TTS_RELEASE_MS === 'number' && POST_TTS_RELEASE_MS > 0) {
    setTimeout(release, POST_TTS_RELEASE_MS);
  } else {
    release();
  }
});

window.addEventListener('local_audio.ended', (event) => {
  runtimeState.audioPlaybackIdle = true;
  const detail = event && event.detail;
  const trigger = detail && detail.uttId === null ? 'local_audio_end_unknown' : 'local_audio_end';
  maybeAutoStartCapture(trigger, 'tts_end');
});

window.addEventListener('assistant.await_user', (event) => {
  runtimeState.audioPlaybackIdle = true;
  const frame = event && event.detail;
  const reason = frame && typeof frame.reason === 'string' ? frame.reason : 'tts_end';
  maybeAutoStartCapture('await_user', reason);
});

window.addEventListener('asr.unavailable', (event) => {
  runtimeState.asrReady = false;
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
  try {
    scheduleAsrRearm();
  } catch (err) {
    console.warn('scheduleAsrRearm failed after asr.unavailable', err);
  }
});

window.addEventListener('asr.ready', (event) => {
  runtimeState.asrReady = true;
  maybeAutoStartCapture('asr_ready', 'policy');
  if (diagHudEnabled()) {
    console.info('diag=asr_ready');
    setBadge('asr:ready');
    sendDiagHudEvent(
      'EVT_CLIENT_ASR_READY',
      event && typeof event === 'object' ? event.detail : undefined,
      { level: 'info', badge: 'asr:ready', message: 'diag=asr_ready' }
    );
  }
  console.info('ui: asr.ready → startMicCaptureIfIdle()');
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
  if (window.AudioRecorder && typeof window.AudioRecorder.startMicCaptureIfIdle === 'function') {
    try {
      const maybe = window.AudioRecorder.startMicCaptureIfIdle();
      if (maybe && typeof maybe.then === 'function' && typeof maybe.catch === 'function') {
        maybe.catch((err) => {
          console.error('AudioRecorder startMicCaptureIfIdle on asr.ready failed', err);
        });
      }
    } catch (err) {
      console.error('AudioRecorder startMicCaptureIfIdle on asr.ready threw', err);
    }
  }
});

window.addEventListener('assistant.suggestions', (event) => {
  const detail = event && event.detail;
  applySuggestionsFrame(detail);
});


// Start mic immediately on ws.open as a safety net; send diag banner
window.addEventListener('ws.open', () => {
  if (audioRecorder && typeof audioRecorder.start === 'function') {
    audioRecorder.start()
      .then(() => {
        setMicPermissionGranted(true, 'ws.open_start');
      })
      .catch(() => {
        setMicPermissionGranted(false, 'ws.open_start_error');
      });
  }
  try {
    if (WSClient && typeof WSClient.send === 'function') {
      WSClient.send({
        type: 'client.banner',
        event: { label: 'ws.open', ts_ms: Date.now() },
        info: { build: window.__BUILD_SHA__ ?? null }
      });
    }
  } catch {}
});
window.addEventListener('ws.close', () => {
  resetSuggestions();
  DiagRecorder.stop('ws');
});

    // --- Smoke test harness (opt-in via ?wsSmoke=1) ---
    if (urlParams.get('wsSmoke') === '1') {
      const transitions = [];
      const unsubscribe = AppState.subscribe((s) => transitions.push(s.connectionState));

      class MockSocket extends EventTarget {
        constructor() {
          super();
          this.readyState = WebSocket.CONNECTING;
          setTimeout(() => {
            this.readyState = WebSocket.OPEN;
            this.dispatchEvent(new Event('open'));
          }, 0);
        }
        send(payload) {
          this.lastSent = payload;
        }
        close(code = 1000, reason = '') {
          this.readyState = WebSocket.CLOSED;
          const ev = new Event('close');
          ev.code = code;
          ev.reason = reason;
          ev.wasClean = true;
          this.dispatchEvent(ev);
        }
        simulateMessage(frame) {
          const payload = typeof frame === 'string' ? frame : JSON.stringify(frame);
          const ev = new Event('message');
          ev.data = payload;
          this.dispatchEvent(ev);
        }
      }

      const sockets = [];
      WSClient.__debug.setTransportFactory(() => {
        const mock = new MockSocket();
        sockets.push(mock);
        return mock;
      });

      startBtn.click();

      setTimeout(() => {
        const mock = sockets[0];
        if (!mock) return;
        mock.simulateMessage({
          type: 'info',
          meta: { sid: 'smoke-sid', resume_token: 'smoke-resume', resume_ttl_ms: 5000 }
        });
        WSClient.__debug.recordPing(Date.now() - 42);
        mock.simulateMessage({ type: 'pong', t: Date.now() });
        setTimeout(() => {
          endBtn.click();
          console.log('WSClient smoke transitions', transitions);
          console.log('WSClient smoke latency', AppState.getState().latencyMs);
          WSClient.__debug.resetTransportFactory();
          unsubscribe();
        }, 20);
      }, 20);
    }
  }

  init();
})();
