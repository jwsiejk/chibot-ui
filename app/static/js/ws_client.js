(() => {
  const HEARTBEAT_INTERVAL_MS = 15000;
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const SUBPROTOCOL = "chat.v2";

  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading WSClient");
  }
  const getAudioPlayer = () => window.AudioPlayer;

  let socket = null;
  let heartbeatTimerId = null;
  let expectInfoFrame = true;
  let lastPingAt = null;
  let transportFactory = (url, protocol) => new WebSocket(url, protocol);
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;

  function updateState(patch) {
    AppState.setState(patch);
  }

  function computeUrl(accessToken, resumeToken) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = `${protocol}//${window.location.host}/ws/v2/chat`;
    const params = new URLSearchParams({ access_token: accessToken });
    if (resumeToken) params.set("resume", resumeToken);
    return `${base}?${params.toString()}`;
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
      const token = state.accessToken;
      if (!token) {
        console.warn("Auto-retry skipped: missing access token");
        return;
      }
      try {
        open(token, { resumeToken: state.resumeToken, skipRateLimitCancel: true });
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
    resetRateLimitRecovery();
    const resumeToken = typeof meta.resume_token === "string" ? meta.resume_token : null;
    const resumeTtlMs = Number.isFinite(meta.resume_ttl_ms) ? meta.resume_ttl_ms : null;
    const expiresAt = resumeToken && resumeTtlMs ? Date.now() + resumeTtlMs : null;
    const descriptor = meta.tts_audio || frame.audio || (frame.meta && frame.meta.audio);
    const audioPlayer = getAudioPlayer();
    if (descriptor && audioPlayer && typeof audioPlayer.setDescriptor === "function") {
      audioPlayer.setDescriptor(descriptor);
    }
    updateState({
      connectionState: "connected",
      sid: meta.sid,
      resumeToken,
      resumeTtlMs,
      resumeExpiresAt: expiresAt,
      infoFrame: frame
    });
    startHeartbeat();
  }

  function handlePongFrame() {
    if (typeof lastPingAt === "number") {
      const latency = Math.max(0, Date.now() - lastPingAt);
      updateState({ latencyMs: latency });
    }
  }

  function handleMessageFrame(frame) {
    if (expectInfoFrame) {
      if (frame.type !== "info") {
        console.error("Expected info frame first, received", frame.type);
        close("bad_info_sequence");
        return;
      }
      handleInfoFrame(frame);
    } else if (frame.type === "info") {
      handleInfoFrame(frame);
    } else if (frame.type === "pong") {
      handlePongFrame();
    } else if (frame.type === "ping") {
      sendJson({ type: "pong", t: Date.now() });
    } else if (frame.type === "error") {
      console.error("WS error frame", frame);
      if (window.WSErrorUI && typeof window.WSErrorUI.handleFrame === "function") {
        try {
          window.WSErrorUI.handleFrame(frame, {
            scheduleRetry: (delayMs, callbacks) => scheduleRateLimitRetry(delayMs, callbacks)
          });
        } catch (err) {
          console.warn("Error handler threw", err);
        }
      }
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
    } else if (frame.type === "policy.interaction") {
      const sanitized = sanitizePolicyFrame(frame);
      dispatchFrame(sanitized);
      return;
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
        if (socket === ws) {
          socket = null;
          expectInfoFrame = true;
        }
        clearHeartbeat();
        updateState({ connectionState: "disconnected", websocket: null });
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
  }

  function cleanupSocket(ws, reason = DEFAULT_CLOSE_REASON) {
    if (!ws) return;
    detachSocket(ws);
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
    }
  }

  function open(accessToken, options = {}) {
    if (!accessToken) {
      throw new Error("accessToken is required to open the chat socket");
    }
    const { resumeToken = null, skipRateLimitCancel = false } = options;
    if (socket) {
      cleanupSocket(socket, "superseded");
    }
    expectInfoFrame = true;
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
    const url = computeUrl(accessToken, resumeToken);
    const ws = transportFactory(url, SUBPROTOCOL);
    socket = ws;
    updateState({
      connectionState: resumeToken ? "resuming" : "connecting",
      accessToken,
      websocket: ws,
      latencyMs: null,
      lastPingAt: null
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
      transportFactory = (url, protocol) => new WebSocket(url, protocol);
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
