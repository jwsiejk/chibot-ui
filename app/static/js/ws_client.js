(() => {
  const HEARTBEAT_INTERVAL_MS = 15000;
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
  const getAudioPlayer = () => window.AudioPlayer;

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
    }, INFO_DEADLINE_MS);
  }

  function clearHeartbeat() {
    if (heartbeatTimerId) {
      clearInterval(heartbeatTimerId);
      heartbeatTimerId = null;
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
        open({ resumeToken, skipRateLimitCancel: true });
      } catch (err) {
        console.error("Auto-retry open failed", err);
      }
    }, delay);
    return true;
  }

  function sendRaw(payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(payload);
  }

  function sendBinary(payload, { dropIfBusy = false } = {}) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      console.warn("WSClient.sendBinary called without an open socket");
      return false;
    }
    if (dropIfBusy && socket.bufferedAmount > 512 * 1024) {
      return false;
    }
    try {
      socket.send(payload);
      return true;
    } catch (err) {
      console.error("WSClient sendBinary error", err);
      return false;
    }
  }

  function sendJson(frame) {
    try {
      sendRaw(JSON.stringify(frame));
    } catch (err) {
      console.error("WSClient sendJson error", err);
    }
  }

  function sendPing() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    lastPingAt = Date.now();
    updateState({ lastPingAt });
    sendJson({ type: "ping" });
  }

  function startHeartbeat() {
    clearHeartbeat();
    sendPing();
    heartbeatTimerId = setInterval(sendPing, HEARTBEAT_INTERVAL_MS);
    updateState({ heartbeatTimerId });
  }

  function attemptAutoResume() {
    const resume = getResumeState();
    if (!resume) {
      return false;
    }
    if (Date.now() >= resume.expiresAt) {
      clearResumeState();
      updateState({ resumeError: "invalid" });
      return false;
    }
    const state = AppState.getState();
    if (autoResumeAttemptToken === resume.token) {
      return false;
    }
    autoResumeAttemptToken = resume.token;
    updateState({ connectionState: "resuming", resumeError: null });
    try {
      open({ resumeToken: resume.token, skipRateLimitCancel: true });
      return true;
    } catch (err) {
      console.error("Auto-resume open failed", err);
      autoResumeAttemptToken = null;
      updateState({ connectionState: "disconnected" });
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
    startHeartbeat();
  }

  function handlePongFrame() {
    if (typeof lastPingAt === "number") {
      const latency = Math.max(0, Date.now() - lastPingAt);
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

    if (frame.type === "ping") {
      sendJson({ type: "pong", t: Date.now() });
      return;
    }

    if (frame.type === "policy.interaction") {
      const sanitized = sanitizePolicyFrame(frame);
      dispatchFrame(sanitized);
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
    } else if (frame.type === "pong") {
      handlePongFrame();
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
    console.warn("Unknown WS frame type", data);
  }

  function attachSocket(ws) {
    ws.__intentionalClose = false;
    const handlers = {
      open: () => {
        updateState({ websocket: ws });
      },
      message: parseFrame,
      error: (event) => {
        console.error("WebSocket error", event);
        window.dispatchEvent(new CustomEvent("ws.error", { detail: event }));
      },
      close: (event) => {
        const expected = ws.__intentionalClose === true;
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
          updateState({ connectionState: "disconnected" });
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
    }
  }

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
    const url = urlOverride || computeUrl(resumeTokenValue);
    const finalUrl = typeof url === "string" ? makeWsUrl(url) : url;
    const wsUrl = typeof finalUrl === "string" ? finalUrl : url;
    const expectsAccessToken = typeof url === "string" && url.includes("access_token=");
    if (expectsAccessToken && (typeof wsUrl !== "string" || !/[?&]access_token=/.test(wsUrl))) {
      console.error("ws_open_missing_token_query", { originalUrl: url, finalUrl: wsUrl });
      showConnectionToast("Session token missing. Please click Start again.");
      updateState({ connectionState: "disconnected" });
      return;
    }
    const wsProtocols = protocols !== undefined ? protocols : SUBPROTOCOL;
    console.log("evt=ws_client_open_params", { url: wsUrl, protocols: wsProtocols });
    const logPayload = Array.isArray(wsProtocols)
      ? { url: wsUrl, protocols: wsProtocols }
      : { url: wsUrl, subprotocol: wsProtocols };
    console.log("WS opening", logPayload);
    const tokenInfo = trackTokenFromUrl(wsUrl);
    const ws = transportFactory(wsUrl, wsProtocols);
    ws.__accessTokenInfo = tokenInfo;
    ws.__handshakeToastShown = false;
    ws.onopen = () => {
      // Lightweight breadcrumb; keep as console.log
      console.log("WebSocket open", { url: wsUrl, protocol: ws.protocol || wsProtocols });
    };

    ws.onerror = (e) => {
      console.error("WebSocket error", e, { readyState: ws.readyState });
      maybeShowHandshakeToast(ws, null);
    };

    ws.onclose = (e) => {
      console.error("WebSocket closed", {
        code: e.code,
        reason: e.reason,
        wasClean: e.wasClean,
        readyState: ws.readyState,
      });
      maybeShowHandshakeToast(ws, e && typeof e.code === "number" ? e.code : null);
    };
    socket = ws;
    updateState({
      connectionState: resumeTokenValue ? "resuming" : "connecting",
      websocket: ws,
      latencyMs: null,
      lastPingAt: null,
      resumeError: null
    });
    attachSocket(ws);
    return ws;
  }

  function close(reason = DEFAULT_CLOSE_REASON) {
    if (!socket) {
      updateState({ connectionState: "disconnected" });
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
    updateState({ connectionState: "disconnected", websocket: null });
  }

  function send(frame) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      console.warn("WSClient.send called without an open socket");
      return;
    }
    const payload = typeof frame === "string" ? frame : JSON.stringify(frame);
    sendRaw(payload);
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

  window.WSClient = {
    open,
    close,
    send,
    sendBinary,
    getBufferedAmount,
    get socket() {
      return socket;
    },
    isConnected() {
      return !!socket && socket.readyState === WebSocket.OPEN && AppState.getState().connectionState === "connected";
    },
    __debug: debug
  };
})();
