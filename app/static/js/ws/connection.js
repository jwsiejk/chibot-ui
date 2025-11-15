// app/static/js/ws/connection.js
// Encapsulates low-level WebSocket connection, queueing, and heartbeat logic.

import { encodeMessagePack } from "../utils/msgpack.mjs";

const HEARTBEAT_INTERVAL_MS = 20000;
const DEFAULT_CLOSE_REASON = "client_shutdown";
const JSON_SUBPROTOCOL = "chat.v2";
const MSGPACK_SUBPROTOCOL = "chip-msgpack";
const INFO_DEADLINE_MS = 20000;
const TOKEN_EXPIRY_MS = 60 * 1000;
const WS_READY_PHASES = new Set(["connected", "ready", "resuming"]);

function detectControlFramesCodec() {
  const normalize = (value) => {
    if (typeof value !== "string") return null;
    const lowered = value.trim().toLowerCase();
    return lowered === "msgpack" ? "msgpack" : lowered === "json" ? "json" : null;
  };
  try {
    const debugValue = normalize(window?.AppState?.debug?.controlFrames);
    if (debugValue) return debugValue;
  } catch {}
  try {
    const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search || "") : null;
    const queryValue = params ? normalize(params.get("controlFrames")) : null;
    if (queryValue) return queryValue;
  } catch {}
  try {
    const stored = typeof window !== "undefined" && window.localStorage ? normalize(window.localStorage.getItem("controlFrames")) : null;
    if (stored) return stored;
  } catch {}
  return "json";
}

const REQUESTED_CONTROL_CODEC = detectControlFramesCodec();
const DEFAULT_SUBPROTOCOLS = REQUESTED_CONTROL_CODEC === "msgpack"
  ? [MSGPACK_SUBPROTOCOL, JSON_SUBPROTOCOL]
  : JSON_SUBPROTOCOL;

let sharedRecordClientBannerEvent = () => {};
let sharedLogStage = () => {};

function isTypedObjectPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return false;
  }
  if (payload instanceof Blob || payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
    return false;
  }
  return true;
}

export function validateOutboundPayload(payload, { rawPayload = payload, source = "ws.connection" } = {}) {
  const recordClientBannerEvent = sharedRecordClientBannerEvent;
  const logStage = sharedLogStage;
  if (!isTypedObjectPayload(payload)) {
    return true;
  }
  const structureTag = Object.prototype.toString.call(payload);
  const isPlainJsonObject = structureTag === "[object Object]";
  if (!isPlainJsonObject) {
    const structure = Array.isArray(payload)
      ? "Array"
      : structureTag.slice(8, -1) || "Unknown";
    const keys = Object.keys(payload || {});
    console.warn("WS connection send skipped payload with non type-preserving structure", { structure, keys });
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "non_type_preserving_structure",
        structure,
        keys: keys.slice(0, 6),
        source,
      });
    } catch {}
    try {
      logStage("client.ws", {
        outcome: "send_skipped_non_type_preserving_structure",
        structure,
        source,
      });
    } catch {}
    return false;
  }
  const type = payload && typeof payload.type === "string" ? payload.type.trim() : "";
  if (type.length > 0) {
    return true;
  }
  const keys = Object.keys(payload || {});
  console.warn("WS connection send skipped object payload without type", { keys, source });
  try {
    recordClientBannerEvent("ws.send.invalid_payload", {
      reason: "missing_type",
      keys: keys.slice(0, 6),
      source,
    });
  } catch {}
  try {
    logStage("client.ws", { outcome: "send_skipped_missing_type", keys: keys.slice(0, 6), source });
  } catch {}
  return false;
}

export function createWsConnection({
  AppState,
  eventEmitter,
  telemetry,
  policyRuntime,
  audioRuntime,
  hubLog,
  handleIncomingFrame: clientHandleIncomingFrame,
}) {
  void policyRuntime;
  const recordClientBannerEvent = telemetry?.recordClientBannerEvent || (() => {});
  const logStage = telemetry?.logStage || (() => {});
  sharedRecordClientBannerEvent = recordClientBannerEvent;
  sharedLogStage = logStage;
  const recordLastError = telemetry?.recordLastError || (() => {});

  const connectionQueue = [];
  let socket = null;
  let negotiatedControlCodec = REQUESTED_CONTROL_CODEC;
  let heartbeatTimerId = null;
  let infoWatchdogTimerId = null;
  let expectInfoFrame = true;
  let lastPingAt = null;
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;
  let lastTokenValue = null;
  let lastTokenMintedAt = null;

  const {
    ensurePcmSender = () => Promise.resolve(null),
    stopInputCapture = () => {},
  } = audioRuntime || {};

  let rawMessageHandler = null;

  function emit(event, payload) {
    try {
      eventEmitter?.emit?.(event, payload);
    } catch (err) {
      console.warn("ws.connection.emit failed", err);
    }
  }

  function updateState(patch) {
    try {
      if (typeof AppState?.setState === "function") {
        AppState.setState(patch);
        return;
      }
    } catch (err) {
      console.warn("ws.connection.updateState setState failed", err);
    }
    try {
      Object.assign(AppState || {}, patch || {});
    } catch {}
  }

  function setAppStateValue(key, value) {
    if (typeof key !== "string" || !key) {
      return;
    }
    try {
      const snapshot = typeof AppState?.getState === "function" ? AppState.getState() : null;
      const current = snapshot && Object.prototype.hasOwnProperty.call(snapshot, key)
        ? snapshot[key]
        : AppState?.[key];
      if (Object.is(current, value)) {
        if (AppState) AppState[key] = value;
        return;
      }
      if (AppState) AppState[key] = value;
      updateState({ [key]: value });
    } catch (err) {
      console.warn("ws.connection.setAppStateValue failed", err);
      try {
        updateState({ [key]: value });
      } catch {}
    }
  }

  function getAppStatePhase() {
    try {
      const snapshot = typeof AppState?.getState === "function" ? AppState.getState() : null;
      const phase = snapshot && typeof snapshot.wsPhase === "string" ? snapshot.wsPhase : AppState?.wsPhase;
      if (typeof phase === "string" && phase) {
        return phase;
      }
      const fallback = snapshot && typeof snapshot.connectionState === "string"
        ? snapshot.connectionState
        : AppState?.connectionState;
      return typeof fallback === "string" && fallback ? fallback : null;
    } catch {
      return null;
    }
  }

  function setWsConnected(connected) {
    setAppStateValue("wsConnected", Boolean(connected));
  }

  function flushQueuedFrames() {
    if (!connectionQueue.length) {
      return;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    const phase = getAppStatePhase();
    if (phase && !WS_READY_PHASES.has(phase)) {
      return;
    }
    while (connectionQueue.length) {
      const entry = connectionQueue.shift();
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const { data, isBinary, options } = entry;
      if (isBinary) {
        const result = sendBinary(data, options || {});
        if (result && typeof result.then === "function") {
          result.catch((err) => console.warn("ws.connection queued binary send failed", err));
        }
        continue;
      }
      send(data, { binary: false, skipPhaseCheck: true });
    }
  }

  function setWsPhase(phase) {
    if (typeof phase !== "string" || !phase) {
      return;
    }
    setAppStateValue("wsPhase", phase);
    if (WS_READY_PHASES.has(phase)) {
      flushQueuedFrames();
    }
  }

  function clearHeartbeat() {
    if (heartbeatTimerId) {
      clearInterval(heartbeatTimerId);
      heartbeatTimerId = null;
    }
    updateState({ heartbeatTimerId: null, lastPingAt: null });
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

  function setNegotiatedControlCodec(codec) {
    negotiatedControlCodec = codec === "msgpack" ? "msgpack" : "json";
  }

  function getNegotiatedControlCodec() {
    return negotiatedControlCodec === "msgpack" ? "msgpack" : "json";
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
    try {
      if (window.WSErrorUI && typeof window.WSErrorUI.clearRateLimitToast === "function") {
        window.WSErrorUI.clearRateLimitToast();
      }
    } catch (err) {
      console.warn("Failed to clear rate limit toast", err);
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
      emit("retry", { attempt: rateLimitRetryCount });
    }, delay);
    return true;
  }

  function canSendHeartbeat() {
    return !!socket && socket.readyState === WebSocket.OPEN;
  }

  function sendPing() {
    if (!canSendHeartbeat()) return;
    lastPingAt = Date.now();
    updateState({ lastPingAt });
    send({ type: "client.ping", ts: lastPingAt });
  }

  function startHeartbeat() {
    clearHeartbeat();
    heartbeatTimerId = setInterval(sendPing, HEARTBEAT_INTERVAL_MS);
    sendPing();
    updateState({ heartbeatTimerId });
  }

  function cloneQueuedPayload(payload, isBinary = false) {
    if (isBinary) {
      if (payload instanceof ArrayBuffer) {
        try {
          return payload.slice(0);
        } catch (err) {
          console.warn("ws.connection queue clone failed (ArrayBuffer)", err);
          return payload;
        }
      }
      if (ArrayBuffer.isView(payload)) {
        try {
          const view = payload;
          return view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength);
        } catch (err) {
          console.warn("ws.connection queue clone failed (TypedArray)", err);
          try { return payload.slice ? payload.slice(0) : payload; } catch { return payload; }
        }
      }
      return payload;
    }
    if (!payload || typeof payload !== "object") {
      return payload;
    }
    try {
      return { ...payload };
    } catch (err) {
      console.warn("ws.connection queue clone failed", err);
      return payload;
    }
  }

  function truncateBannerString(value, max = 240) {
    if (typeof value !== "string") {
      return value;
    }
    if (value.length <= max) {
      return value;
    }
    return `${value.slice(0, max - 3)}...`;
  }

  function isControlFrame(frame) {
    if (!frame || typeof frame !== "object") return false;
    const t = typeof frame.type === "string" ? frame.type : "";
    return t === "input.start" || t === "input.stop" || t === "audio.header" || t === "ping" || t === "pong";
  }

  function encodeControlFramePayload(frame, codec) {
    if (codec === "msgpack") {
      try {
        const encoded = encodeMessagePack(frame);
        return { binary: true, payload: encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength) };
      } catch (err) {
        console.warn("WS connection encode msgpack failed", err);
        return null;
      }
    }
    if (typeof frame === "string") {
      return { binary: false, payload: frame };
    }
    try {
      return { binary: false, payload: JSON.stringify(frame) };
    } catch (err) {
      console.warn("WS connection stringify control frame failed", err);
      return null;
    }
  }

  function extractArrayBuffer(payload) {
    if (payload instanceof ArrayBuffer) {
      return payload;
    }
    if (ArrayBuffer.isView(payload)) {
      try {
        return payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
      } catch (err) {
        console.warn("WS connection binary guard: buffer slice failed", err);
        return null;
      }
    }
    return null;
  }

  const BINARY_JSON_GUARD_MAX_BYTES = 512;

  function decodeBinaryJsonCandidate(payload) {
    const buffer = extractArrayBuffer(payload);
    if (!buffer) {
      return null;
    }
    if (!buffer.byteLength || buffer.byteLength > BINARY_JSON_GUARD_MAX_BYTES) {
      return null;
    }
    try {
      const view = new Uint8Array(buffer);
      for (let i = 0; i < view.length; i += 1) {
        if (view[i] === 0) {
          return null;
        }
      }
      const text = new TextDecoder("utf-8", { fatal: false }).decode(view).trim();
      if (!text || (text[0] !== "{" && text[0] !== "[")) {
        return null;
      }
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object") {
        return null;
      }
      return { parsed, text };
    } catch (err) {
      console.warn("WS connection binary guard: decode failed", err);
      return null;
    }
  }

  function handleBinaryJsonPayload(payload, { source = "ws.connection.binary" } = {}) {
    const decoded = decodeBinaryJsonCandidate(payload);
    if (!decoded) {
      return null;
    }
    const candidate = decoded.parsed;
    if (!validateOutboundPayload(candidate, { rawPayload: decoded.text, source })) {
      return false;
    }
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "binary_json_payload",
        source,
      });
    } catch {}
    return candidate;
  }

  function logTransportMisuse(kind) {
    try {
      console.warn("WS misuse:", kind);
    } catch {}
    try {
      hubLog?.("client.ws.misuse", { kind });
    } catch (err) {
      console.warn("WS misuse hub.log failed", err);
    }
  }

  function queueFrame(data, isBinary, options) {
    connectionQueue.push({ data: cloneQueuedPayload(data, isBinary), isBinary, options });
  }

  function send(payload, { binary = false, skipPhaseCheck = false } = {}) {
    let data = payload;

    if (!binary && (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload))) {
      console.debug("ws.connection.send: binary payload ignored by JSON helper");
      return false;
    }

    if (!binary) {
      if (!validateOutboundPayload(data, { source: "ws.connection.send" })) {
        return false;
      }
      if (!data || typeof data !== "object") {
        console.warn("ws.connection.send blocked: missing or invalid type", payload);
        return false;
      }
      if (typeof data.type !== "string") {
        console.warn("ws.connection.send blocked: missing or invalid type", payload);
        return false;
      }
      if (data.type === "audio.header") {
        if (data.format !== "pcm16" || typeof data.sample_rate !== "number" || typeof data.channels !== "number") {
          console.warn("ws.connection.send blocked: invalid audio.header schema", payload);
          return false;
        }
        data = {
          type: "audio.header",
          format: "pcm16",
          sample_rate: Number(data.sample_rate),
          channels: Number(data.channels),
        };
      }
    }

    const live = socket;
    const open = !!live && live.readyState === WebSocket.OPEN;
    const isControl = !binary && isControlFrame(data);

    if (!skipPhaseCheck && !binary && !isControl) {
      const phase = getAppStatePhase();
      if (phase && !WS_READY_PHASES.has(phase)) {
        queueFrame(data, false);
        console.warn("ws.connection.send queued (phase not ready)", { phase });
        return true;
      }
    }

    if (!open) {
      queueFrame(data, !!binary);
      console.warn("ws.connection.send queued (socket not open)");
      return true;
    }

    try { live.binaryType = "arraybuffer"; } catch {}

    if (!binary && isControl) {
      const codec = getNegotiatedControlCodec();
      const encoded = encodeControlFramePayload(data, codec);
      if (!encoded) {
        return false;
      }
      try {
        live.send(encoded.payload);
        return true;
      } catch (err) {
        console.error("ws.connection send error", err);
        return false;
      }
    }

    if (binary) {
      if (payload instanceof Blob) {
        return payload.arrayBuffer().then((buf) => {
          try {
            live.send(buf);
            return true;
          } catch (err) {
            console.error("ws.connection binary send error", err);
            throw err;
          }
        });
      }
      if (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
        const buffer = payload instanceof ArrayBuffer
          ? payload
          : payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
        try {
          live.send(buffer);
          return true;
        } catch (err) {
          console.error("ws.connection binary send error", err);
          return false;
        }
      }
      logTransportMisuse("send_binary_invalid_payload");
      return false;
    }

    const text = typeof data === "string" ? data : JSON.stringify(data);
    try {
      live.send(text);
      return true;
    } catch (err) {
      console.error("ws.connection send error", err);
      return false;
    }
  }

  function sendBinary(payload, opts = {}) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      queueFrame(payload, true, opts && typeof opts === "object" ? { ...opts } : undefined);
      return false;
    }
    if (payload instanceof Blob) {
      return payload.arrayBuffer().then((buf) => {
        const jsonCandidate = handleBinaryJsonPayload(buf, { source: "ws.connection.binary_blob" });
        if (jsonCandidate === false) {
          return false;
        }
        if (jsonCandidate) {
          return send(jsonCandidate, { binary: false });
        }
        try {
          socket.send(buf);
        } catch (err) {
          console.warn("ws.connection binary send failed", err);
          throw err;
        }
        return true;
      });
    }
    const jsonCandidate = handleBinaryJsonPayload(payload, { source: "ws.connection.binary" });
    if (jsonCandidate === false) {
      return false;
    }
    if (jsonCandidate) {
      return send(jsonCandidate, { binary: false });
    }
    try {
      socket.send(payload);
    } catch (err) {
      console.warn("ws.connection binary send failed", err);
      return false;
    }
    return true;
  }

  function getBufferedAmount() {
    if (!socket) return 0;
    return socket.bufferedAmount || 0;
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
    setWsPhase("closing");
    setWsConnected(false);
    detachSocket(ws);
    ws.__intentionalClose = true;
    recordClientBannerEvent("ws.cleanup", { reason: truncateBannerString(reason || "", 80) });
    try {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close(1000, reason);
      }
    } catch (err) {
      console.warn("ws.connection cleanup close error", err);
    }
    if (socket === ws) {
      try {
        const maybePromise = typeof ensurePcmSender === "function" ? ensurePcmSender() : null;
        if (maybePromise && typeof maybePromise.then === "function") {
          maybePromise.then((sender) => {
            if (sender && typeof sender.setWebSocket === "function") {
              try {
                sender.setWebSocket(null);
              } catch (err) {
                console.warn("pcm.sender.detach_failed", err);
              }
            }
          }).catch((err) => console.warn("pcm.sender.detach_failed", err));
        }
      } catch (err) {
        console.warn("pcm.sender.detach_failed", err);
      }
      socket = null;
      expectInfoFrame = true;
      clearInfoWatchdog();
      if (typeof stopInputCapture === "function") {
        try {
          stopInputCapture();
        } catch (err) {
          console.warn("ws.connection stopInputCapture failed", err);
        }
      }
      try {
        if (window.ws === ws) {
          window.ws = null;
        }
      } catch {}
    }
  }

  function handleInfoFrame(frame) {
    try {
      updateState({ infoFrame: frame });
      recordClientBannerEvent("ws.info", frame ? { has_policy: Boolean(frame?.policy) } : null);
    } catch {}
  }

  function handleServerBanner(frame) {
    try {
      updateState({ serverBanner: frame });
    } catch {}
  }

  function processIncomingFrame(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    if (frame.type === "info") {
      expectInfoFrame = false;
      clearInfoWatchdog();
      handleInfoFrame(frame);
    }
    if (frame.type === "server.banner") {
      handleServerBanner(frame);
    }
    clientHandleIncomingFrame?.(frame);
  }

  function setRawMessageHandler(handler) {
    rawMessageHandler = typeof handler === "function" ? handler : null;
  }

  function attachSocket(ws) {
    ws.__intentionalClose = false;
    const handlers = {
      open: () => {
        const negotiated = typeof ws?.protocol === "string" && ws.protocol === MSGPACK_SUBPROTOCOL
          ? "msgpack"
          : "json";
        setNegotiatedControlCodec(negotiated);
        setWsConnected(true);
        setWsPhase("connected");
        recordLastError(null, null);
        updateState({ websocket: ws, connectionState: "connected" });
        try { window.ws = ws; } catch {}
        startHeartbeat();
        resetRateLimitRecovery();
        emit("open", { websocket: ws });
        logStage("client.ws", { outcome: "connected", subprotocol: ws?.protocol || null });
      },
      message: (event) => {
        try {
          if (rawMessageHandler) {
            rawMessageHandler(event.data);
            return;
          }
          console.warn("ws.connection message handler missing parser; dropping frame");
        } catch (err) {
          console.error("WS message handler critical crash", err);
          hubLog?.("client.ws.crash", { error: err?.message, source: "onmessage" });
        }
      },
      error: (event) => {
        console.error("WebSocket error", event);
        const message = event && typeof event?.message === "string" && event.message
          ? event.message
          : "socket_error";
        recordLastError(null, message);
        emit("error", event);
      },
      close: (event) => {
        const expected = ws.__intentionalClose === true;
        const detailReason = event && typeof event?.reason === "string" && event.reason
          ? event.reason
          : (expected ? "intentional_close" : "ws_close");
        recordLastError(event && typeof event?.code === "number" ? event.code : null, detailReason);
        setWsConnected(false);
        setWsPhase("disconnected");
        logStage("client.ws", { outcome: "close", code: event?.code, reason: event?.reason });
        if (socket === ws) {
          socket = null;
          expectInfoFrame = true;
          clearInfoWatchdog();
        }
        clearHeartbeat();
        updateState({ websocket: null });
        emit("close", event);
        setNegotiatedControlCodec(REQUESTED_CONTROL_CODEC);
        maybeShowHandshakeToast(ws, event?.code);
      },
    };
    ws.addEventListener("open", handlers.open);
    ws.addEventListener("message", handlers.message);
    ws.addEventListener("error", handlers.error);
    ws.addEventListener("close", handlers.close);
    ws.__handlers = handlers;
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
    emit("toast", { message });
    ws.__handshakeToastShown = true;
    recordClientBannerEvent("ws.handshake.toast", {
      message,
      minted_ms: mintedAt,
      close_code: closeCode,
    });
  }

  function open(url, token, options = {}) {
    const opts = options && typeof options === "object" ? options : {};
    const { protocols = DEFAULT_SUBPROTOCOLS, resumeToken = null, skipRateLimitCancel = false } = opts;

    if (socket) {
      cleanupSocket(socket, "superseded");
    }
    expectInfoFrame = true;
    startInfoWatchdog();
    if (!skipRateLimitCancel) {
      clearRateLimitRetryTimer();
      rateLimitRetryCount = 0;
    }

    const wsUrl = typeof url === "string" ? url : null;
    if (!wsUrl) {
      console.error("ws.connection.open missing url");
      return null;
    }

    const bannerUrlMeta = typeof wsUrl === "string" ? wsUrl : undefined;
    recordClientBannerEvent("ws.open.request", {
      resume: Boolean(resumeToken),
      skip_rate_limit_cancel: Boolean(skipRateLimitCancel),
      has_access_token: typeof token === "string" && token.length > 0,
      url: bannerUrlMeta,
      protocols: Array.isArray(protocols)
        ? protocols.filter((proto) => typeof proto === "string" && proto).slice(0, 4)
        : (typeof protocols === "string" && protocols ? [protocols] : undefined),
    });
    console.log("evt=ws_connection_open_params", { url: wsUrl, protocols });

    setWsConnected(false);
    setWsPhase(resumeToken ? "resuming" : "connecting");
    recordLastError(null, null);

    const tokenInfo = trackTokenFromUrl(wsUrl);
    const ws = new WebSocket(wsUrl, protocols);
    ws.__accessTokenInfo = tokenInfo;
    socket = ws;
    attachSocket(ws);
    return ws;
  }

  function close(reason = DEFAULT_CLOSE_REASON) {
    const normalizedReason = typeof reason === "string" && reason ? reason : DEFAULT_CLOSE_REASON;
    recordClientBannerEvent("ws.close.request", { reason: truncateBannerString(normalizedReason || "", 80) });
    cleanupSocket(socket, normalizedReason);
    clearHeartbeat();
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    updateState({ connectionState: "disconnected", websocket: null, infoFrame: null, serverBanner: null });
  }

  return {
    open,
    close,
    send,
    sendBinary,
    getBufferedAmount,
    handleParsedFrame: processIncomingFrame,
    setRawMessageHandler,
    getNegotiatedControlCodec,
  };
}
