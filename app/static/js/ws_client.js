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

  function handleInfoFrame(frame) {
    const meta = frame && frame.meta;
    if (!meta || typeof meta.sid !== "string") {
      console.error("Invalid info frame", frame);
      close("bad_info_frame");
      return;
    }
    expectInfoFrame = false;
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

  function open(accessToken, { resumeToken = null } = {}) {
    if (!accessToken) {
      throw new Error("accessToken is required to open the chat socket");
    }
    if (socket) {
      cleanupSocket(socket, "superseded");
    }
    expectInfoFrame = true;
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
