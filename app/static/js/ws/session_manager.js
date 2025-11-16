// app/static/js/ws/session_manager.js
// Encapsulates WS session lifecycle, resume, and rate-limit retry logic.

export function createSessionManager({
  AppState,
  connection,                // createWsConnection(...)
  captureRuntime = {},       // createCaptureRuntime(...) for stopRecorder/evaluateStopRecorderReason
  bannerClient = {},         // createBannerClient(...)
  logStage = () => {},       // telemetry logStage
  recordClientBannerEvent = () => {}, // telemetry banner helper
  hubLog = () => {},         // hub logging helper
  recordLastError = () => {},
  DEFAULT_SUBPROTOCOLS = [],
  DEFAULT_CLOSE_REASON = "client_shutdown",
  TOKEN_EXPIRY_MS = 60 * 1000,
  getAudioStreaming = () => false,
  setAudioStreaming = () => {},
  ensurePcmSender = () => Promise.resolve(null),
  resetAudioHeaderSent = () => {},
  isTypedObjectPayload = () => false,
  validateOutboundPayload = () => true,
}) {
  const {
    stopRecorder = async () => {},
    evaluateStopRecorderReason = () => ({ }),
    setAsrArmInFlight = () => {},
    setAppStateValue = () => {},
    setWsConnected = () => {},
    setWsPhase = () => {},
  } = captureRuntime || {};

  const {
    showConnectionToast = () => {},
    truncateBannerString = (value) => value,
    sanitizeUrlForBanner = () => undefined,
  } = bannerClient || {};

  const WSClient = (typeof window !== "undefined")
    ? (window.WSClient = window.WSClient || {})
    : {};

  let socket = null;
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;
  let autoResumeAttemptToken = null;
  let lastTokenValue = null;
  let lastTokenMintedAt = null;

  function updateState(patch) {
    if (!patch || typeof patch !== "object") {
      return;
    }
    try {
      if (typeof AppState?.setState === "function") {
        AppState.setState(patch);
      }
    } catch {}
  }

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
      try { console.warn("Failed to construct WS URL", err); } catch {}
      return trimmed;
    }
  }

  function resolveWsPath() {
    try {
      const routing = AppState?.policy?.routing
        || (AppState?.policy?.policy?.routing);
      const candidate = typeof routing?.ws_version === "string" ? routing.ws_version.trim() : "";
      if (candidate && candidate.toLowerCase() !== "v2") {
        console.warn("Unsupported ws_version from policy; normalizing to v2", candidate);
      }
    } catch (err) {
      try { console.warn("Failed to inspect policy routing version", err); } catch {}
    }
    return "/ws/v2/chat";
  }

  function computeUrl(resumeToken) {
    const protocol = typeof window !== "undefined" && window.location?.protocol === "https:" ? "wss:" : "ws:";
    const host = typeof window !== "undefined" ? window.location?.host : undefined;
    if (!protocol || !host) {
      return null;
    }
    const base = `${protocol}//${host}${resolveWsPath()}`;
    const params = new URLSearchParams();
    if (typeof resumeToken === "string" && resumeToken.trim()) {
      params.set("resume", resumeToken.trim());
    }
    const query = params.toString();
    return query ? `${base}?${query}` : base;
  }

  function getResumeState() {
    const state = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
    const resume = state && typeof state.resume === "object" ? state.resume : null;
    if (!resume) return null;
    const token = typeof resume.token === "string" ? resume.token : null;
    const ttlMs = Number.isFinite(resume.ttlMs) ? resume.ttlMs : null;
    const expiresAt = Number.isFinite(resume.expiresAt) ? resume.expiresAt : null;
    if (!token || !ttlMs || !expiresAt) return null;
    return { token, ttlMs, expiresAt };
  }

  function assignResume(token, ttlMs) {
    if (typeof AppState?.setResume === "function") {
      AppState.setResume(token, ttlMs);
    }
    autoResumeAttemptToken = null;
  }

  function clearResumeState() {
    if (typeof AppState?.clearResume === "function") {
      AppState.clearResume();
    }
    autoResumeAttemptToken = null;
  }

  function trackTokenFromUrl(url) {
    if (typeof url !== "string" || !url) {
      return { token: null, mintedAt: null };
    }
    let token = null;
    try {
      const origin = typeof window !== "undefined" && window.location ? window.location.origin : undefined;
      const parsed = new URL(url, origin);
      token = parsed.searchParams.get("access_token");
    } catch {
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
    } catch {
      return undefined;
    }
  }

  function maybeShowHandshakeToast(candidateSocket, closeCode) {
    const ws = candidateSocket && typeof candidateSocket === "object" ? candidateSocket : null;
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

  function clearRateLimitRetryTimer() {
    if (rateLimitRetryTimerId) {
      try { clearTimeout(rateLimitRetryTimerId); } catch {}
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
      let resumeToken = null;
      try {
        const state = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
        const resumeState = state && typeof state.resume === "object" ? state.resume : null;
        if (
          resumeState &&
          typeof resumeState.token === "string" &&
          Number.isFinite(resumeState.expiresAt) &&
          Date.now() < resumeState.expiresAt
        ) {
          resumeToken = resumeState.token;
        }
      } catch {}
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

  function getSocket() {
    return socket;
  }

  function setSocket(next) {
    socket = next;
    if (typeof window !== "undefined") {
      try { window.ws = next; } catch {}
    }
  }

  function isAudioStreamingActive() {
    try {
      return Boolean(getAudioStreaming());
    } catch {
      return false;
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

    const liveSocket = getSocket();
    if (liveSocket && (liveSocket.readyState === WebSocket.OPEN || liveSocket.readyState === WebSocket.CONNECTING)) {
      return liveSocket;
    }
    if (liveSocket) {
      connection.close("superseded");
    }
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
      return null;
    }

    const wsProtocols = protocols !== undefined ? protocols : DEFAULT_SUBPROTOCOLS;
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

    setWsConnected(false);
    setWsPhase(resumeTokenValue ? "resuming" : "connecting");
    recordLastError(null, null);

    const ws = connection.open(wsUrl, null, {
      protocols: wsProtocols,
      resumeToken: resumeTokenValue,
      skipRateLimitCancel,
    });
    if (!ws) {
      return null;
    }
    setSocket(ws);
    try { WSClient._ws = ws; } catch {}

    const originalSend = typeof ws.send === "function" ? ws.send : null;
    if (originalSend) {
      const boundOriginalSend = originalSend.bind(ws);
      ws.send = function patchedSend(data, ...rest) {
        const target = (this && typeof this === "object") ? this : ws;
        const isBinaryPayload = data instanceof Blob || data instanceof ArrayBuffer || ArrayBuffer.isView(data);
        if (!isBinaryPayload && isTypedObjectPayload(data)) {
          try {
            if (!validateOutboundPayload(data, { rawPayload: data, source: "ws_instance" })) {
              return undefined;
            }
            data = JSON.stringify(data);
          } catch (err) {
            console.warn("WSClient send wrapper: serialization failed", err);
            return undefined;
          }
        }
        try {
          target.__wsClientGuarding = true;
          return boundOriginalSend(data, ...rest);
        } finally {
          try { delete target.__wsClientGuarding; } catch { target.__wsClientGuarding = undefined; }
        }
      };
      ws.__originalSend = function delegatingOriginalSend(data, ...rest) {
        return ws.send.call(ws, data, ...rest);
      };
    }

    try { ws.binaryType = "arraybuffer"; } catch (err) { console.warn("Failed to set WebSocket binaryType", err); }

    if (isAudioStreamingActive()) {
      ensurePcmSender().then((sender) => {
        if (sender && typeof sender.setWebSocket === "function") {
          try { sender.setWebSocket(ws); } catch (err) { console.warn("pcm.sender.attach_failed", err); }
        }
      }).catch((err) => { console.warn("pcm.sender.attach_failed", err); });
    }

    updateState({
      connectionState: resumeTokenValue ? "resuming" : "connecting",
      websocket: ws,
      latencyMs: null,
      lastPingAt: null,
      resumeError: null,
      infoFrame: null,
      serverBanner: null,
    });
    return ws;
  }

  async function close(reason = DEFAULT_CLOSE_REASON) {
    const evaluation = evaluateStopRecorderReason(reason, DEFAULT_CLOSE_REASON);
    const normalizedReason = typeof evaluation?.label === "string" && evaluation.label
      ? evaluation.label
      : (typeof reason === "string" && reason) || DEFAULT_CLOSE_REASON;
    if (evaluation?.blocked && !evaluation?.allowed) {
      try {
        console.info("WSClient.close ignored for VAD/mic trigger", { reason: normalizedReason });
      } catch {}
      return;
    }
    const closeReason = normalizedReason || DEFAULT_CLOSE_REASON;
    const wasStreaming = isAudioStreamingActive();
    if (wasStreaming) {
      const offReason = closeReason || "client_shutdown";
      hubLog("client.stream.off", { reason: offReason });
    }
    setAudioStreaming(false);
    recordClientBannerEvent("ws.close.request", { reason: truncateBannerString(closeReason || "", 80) });
    try { resetAudioHeaderSent(); } catch {}
    await stopRecorder(closeReason || "client_shutdown", { source: "ws.close" });
    setAsrArmInFlight(false);
    setAppStateValue("ttsActive", false);
    setWsPhase("closing");
    setWsConnected(false);
    const emitResumeInvalid = () => {
      if (closeReason === "resume_invalid" && typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        try {
          window.dispatchEvent(new CustomEvent("ws.resume_invalid", { detail: { reason: closeReason } }));
        } catch {}
      }
    };
    connection.close(closeReason);
    if (window.WSErrorUI && typeof window.WSErrorUI.cancelRateLimitCountdown === "function") {
      try {
        window.WSErrorUI.cancelRateLimitCountdown(closeReason);
      } catch (err) {
        console.warn("Failed to cancel countdown on close", err);
      }
    }
    setSocket(null);
    try { WSClient._ws = null; } catch {}
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    autoResumeAttemptToken = null;
    emitResumeInvalid();
    updateState({ connectionState: "disconnected", websocket: null, infoFrame: null, serverBanner: null });
  }

  return {
    makeWsUrl,
    computeUrl,
    getResumeState,
    assignResume,
    clearResumeState,
    attemptAutoResume,
    trackTokenFromUrl,
    maybeShowHandshakeToast,
    resetRateLimitRecovery,
    scheduleRateLimitRetry,
    open,
    close,
  };
}
