// CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram; no wake word.
/* __BUILD_MARKER__: FULL_DUPLEX_01 */
import { initVAD } from "./audio/vad_client.js";
import { initPcmSender } from "./audio/pcm_sender.js";
import { createWsAudioRuntime } from "./audio/ws_audio_runtime.js";
import { createPolicyRuntime } from "./ws/policy_runtime.js";
import { createWsConnection } from "./ws/connection.js";
import { createTurnRuntime } from "./ws/turns.js";
import { createBannerClient } from "./ws/banner_client.js";
import { encodeMessagePack, decodeMessagePack } from "./utils/msgpack.mjs";
import {
  MIC_OUTCOME,
  logMic,
  emitMicBreadcrumb,
  normalizeErrorDetail,
  recordLastError,
  recordClientBannerEvent,
  logStage,
} from "./ws/telemetry.js";
(() => {
  // ===== Shared constants, policy defaults, tiny helpers =====
  const HEARTBEAT_INTERVAL_MS = 20000;
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const JSON_SUBPROTOCOL = "chat.v2";
  const MSGPACK_SUBPROTOCOL = "chip-msgpack";
  const REQUESTED_CONTROL_CODEC = detectControlFramesCodec();
  const DEFAULT_SUBPROTOCOLS = REQUESTED_CONTROL_CODEC === "msgpack"
    ? [MSGPACK_SUBPROTOCOL, JSON_SUBPROTOCOL]
    : JSON_SUBPROTOCOL;
  const INFO_DEADLINE_MS = 20000;
  const TOKEN_EXPIRY_MS = 60 * 1000;
  const MAX_GATE_SILENCE_MS = 3000;
  // server_no_speech_timeout_ms should be ≥ 2 × MAX_GATE_SILENCE_MS to let the client close the turn cleanly.
  const VAD_SILENCE_TIMEOUT_SAMPLE_RATE = 10;

  const IGNORED_VENDOR_MESSAGES = new Set(["AddPartialTranscript", "AddTranscript"]);
  const PCM_BREADCRUMB_POLICY = { input: 'pcm_16k', mode: 'pcm16' };
  const PCM_TARGET_SAMPLE_RATE = 16000;
  const DEFAULT_ASR_VENDOR = 'gcp';
  const WS_READY_PHASES = new Set(['connected', 'ready', 'resuming']);
  let negotiatedControlCodec = REQUESTED_CONTROL_CODEC;

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

  // ===== Mic + VAD state, breadcrumbs, and telemetry wiring =====
  // ---- Golden-path turn trace & mic outcomes (additive) ----
  // ---- Telemetry (additive) ----
  let __micAttempts = 0;
  let __micChunks = 0;
  let __micBytes = 0;
  let __micPermissionGranted = false;
  let __micRecordingStartAt = null;
  let __micFirstChunkBreadcrumbSent = false;
  let __turnTraceId = null; // optional trace id per turn (sid + timestamp)

  let runtimeResetTurnIntent = null;
  let runtimeCanCaptureNow = null;
  let runtimeOpenAsr = null;
  let runtimeRequestAsrArm = null;
  let runtimeRequestAsrClose = null;
  let runtimeRecoverFromAsrFault = null;
  let runtimeHandleAsrStateFrame = null;

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
  // ---- Debug toggles (runtime-settable) ----
  function dbg(key, fallback = false) {
    try {
      return !!(window.AppState?.debug && window.AppState.debug[key]);
    } catch {
      return fallback;
    }
  }

  let __micArmedAt = 0;     // ms since epoch
  let __firstChunkSeen = false;
  let __armingGraceUntil = 0; // ms epoch; brief window after capture start
  let __pauseSendUntil = 0;
  let __throttleTimer = null;

  let _audioStreaming = false;
  let awaitingAsrClosedAck = false;
  let pendingAsrClosedSeq = null;
  let awaitingTurnEndForRearm = false;
  let pendingRearmReason = null;
  let asrRecovering = false;
  const primedSessionIds = new Set();
  let partialWatchdogTimer = null;
  let partialWatchdogDeadline = 0;
  let partialWatchdogFirstTurn = true;

  function clearPartialWatchdog() {
    if (partialWatchdogTimer) {
      try { clearTimeout(partialWatchdogTimer); } catch {}
      partialWatchdogTimer = null;
    }
    partialWatchdogDeadline = 0;
  }

  function schedulePartialWatchdog(source) {
    const firstTurn = partialWatchdogFirstTurn === true;
    const rawDelay = firstTurn ? WATCHDOG_FIRST_MS : WATCHDOG_SUBSEQUENT_MS;
    const delay = Number.isFinite(rawDelay) ? Math.max(0, rawDelay) : 0;
    partialWatchdogFirstTurn = false;
    if (delay <= 0) {
      clearPartialWatchdog();
      return;
    }

    clearPartialWatchdog();
    const reason = typeof source === "string" && source ? source : "unknown";
    partialWatchdogDeadline = Date.now() + delay;

    partialWatchdogTimer = setTimeout(() => {
      partialWatchdogTimer = null;
      partialWatchdogDeadline = 0;
      try {
        logStage("client.watchdog.partial_timeout", {
          source: reason,
          first_turn: firstTurn,
          wait_ms: delay,
        });
      } catch {}

      try {
        recordClientBannerEvent("watchdog.partial.timeout", {
          source: reason,
          first_turn: firstTurn,
          wait_ms: delay,
        });
      } catch {}

      try {
        if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
          window.dispatchEvent(new CustomEvent("client.watchdog.partial_timeout", {
            detail: { source: reason, firstTurn, waitMs: delay },
          }));
        }
      } catch {}

      try {
        void recoverFromAsrFault("partial_timeout");
      } catch (err) {
        try { console.warn("Partial watchdog recovery failed", err); } catch (_) {}
      }
    }, delay);
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
      Object.defineProperty(window, "__micArmedAt", {
        configurable: true,
        get() { return __micArmedAt; },
        set(value) {
          if (Number.isFinite(value)) {
            __micArmedAt = value;
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
    } catch {}
  }

  // ===== Client policy runtime (applyPolicySnapshotFromSource, etc.) =====
  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading WSClient");
  }
  function updateState(patch) {
    AppState.setState(patch);
  }

  let socket = null;

  function getSocket() {
    return socket;
  }

  const bannerClient = createBannerClient({
    AppState,
    updateState,
    getSocket,
    sendJson,
  });

  const {
    CLIENT_BANNER_TYPE,
    CLIENT_BANNER_MAX_HISTORY,
    CLIENT_BANNER_MAX_QUEUE,
    CLIENT_BANNER_EVENT_LABEL_MAX,
    CLIENT_BANNER_STRING_MAX,
    ensureClientBannerState,
    updateClientBannerState,
    queueClientBannerPayload,
    flushClientBannerQueue,
    ensureToastRoot,
    showConnectionToast,
    truncateBannerString,
    sanitizeBannerValue,
    sanitizeUrlForBanner,
    collectClientBannerInfo,
  } = bannerClient;

  const ASR_RATE = (AppState?.targetSampleRate || 16000);
  // ===== PCM sender + ring buffer + ASR priming =====
  

  let scheduleAudioKeepaliveImpl = () => {};
  let clearAudioKeepaliveTimerImpl = () => {};

  function clearAudioKeepaliveTimer() {
    try {
      clearAudioKeepaliveTimerImpl();
    } catch (err) {
      console.warn("clearAudioKeepaliveTimer failed", err);
    }
  }

  function scheduleAudioKeepalive() {
    try {
      scheduleAudioKeepaliveImpl();
    } catch (err) {
      console.warn("scheduleAudioKeepalive failed", err);
    }
  }

  if (typeof AppState._recoverPrimePending === "undefined") {
    AppState._recoverPrimePending = false;
  }
  const FEATURE_LEGACY_POLICY = Boolean(window.FEATURE_LEGACY_POLICY ?? false);

  const policyRuntime = createPolicyRuntime(AppState, {
    updateState,
    dispatchFrame,
    reasonLooksUserInitiated,
  });

  const {
    getCurrentPolicy,
    applyPolicySnapshotFromSource,
    installClientVadPolicySnapshot,
    shouldAutoRearmAfterClosed,
    getClientVadPolicyRoot,
  } = policyRuntime;

  installClientVadPolicySnapshot();

  // Derive POLICY *after* defaults are merged in
  const POLICY = AppState && typeof AppState.policy === "object" ? AppState.policy : {};
  const POLICY_VAD = POLICY?.vad || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.vad : {});
  const POLICY_WATCHDOG = POLICY?.watchdog || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.watchdog : {});
  const POLICY_STATUS = POLICY?.ui?.status
    || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.ui?.status : undefined);
  const WATCHDOG_FIRST_MS = Number(
    POLICY_WATCHDOG?.partial_wait_ms_first_turn ?? 3500,
  );
  const WATCHDOG_SUBSEQUENT_MS = Number(
    POLICY_WATCHDOG?.partial_wait_ms ?? 2500,
  );
  const SENDER_GATE_ON_TTS = Boolean(
    POLICY_VAD?.sender_gate_on_tts ?? true,
  );
  const REQUIRE_ACTIVE_TURN = Boolean(
    POLICY_STATUS?.require_active_turn ?? true,
  );

  const appStateEventEmitter = createEventEmitter();
  if (typeof AppState.on !== "function") {
    AppState.on = (event, handler) => appStateEventEmitter.on(event, handler);
  }
  if (typeof AppState.emit !== "function") {
    AppState.emit = (event, detail) => appStateEventEmitter.emit(event, detail);
  }
  if (typeof AppState.on === "function" && !AppState.__pcmBreadcrumbHandlersInstalled) {
    AppState.__pcmBreadcrumbHandlersInstalled = true;
    AppState.on("recordingStarted", () => {
      __micRecordingStartAt = Date.now();
      __micFirstChunkBreadcrumbSent = false;
      emitMicBreadcrumb({ event: "recording_start", policy: { ...PCM_BREADCRUMB_POLICY } });
    });
  }

  let userGestureSatisfied = !AppState.policy.require_user_gesture_first_visit;

  // ===== WebSocket connection + queue + outbound send helpers =====
  const WSClient = window.WSClient = window.WSClient || {};
  if (typeof window !== "undefined" && typeof window.ws === "undefined") {
    window.ws = null;
  }
  const wsEventEmitter = WSClient.__events = WSClient.__events || createEventEmitter();
  setNegotiatedControlCodec(REQUESTED_CONTROL_CODEC);
  WSClient.on = function on(event, handler) {
    return wsEventEmitter.on(event, handler);
  };
  WSClient.off = function off(event, handler) {
    return wsEventEmitter.off(event, handler);
  };
  WSClient.emit = function emit(event, detail) {
    return wsEventEmitter.emit(event, detail);
  };
  WSClient._ws = WSClient._ws || null;
  WSClient.__firstChunkSeen = () => __firstChunkSeen === true;
  const getAudioPlayer = () => window.AudioPlayer;

  let expectInfoFrame = true;
  let infoWatchdogTimerId = null;
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;
  let autoResumeAttemptToken = null;
  let lastTokenValue = null;
  let lastTokenMintedAt = null;

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

  // ---- Turn opener (idempotent + retry) ----
  let __turnOpen = false, __turnOpenAt = 0;
  async function openTurnOnce(reason) {
    if (__turnOpen) return true;
    const ws = () => (WSClient?.socket || window.ws);
    const deadline = Date.now() + 1200;
    while ((!ws() || ws().readyState !== WebSocket.OPEN) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 50));
    }
    if (!ws() || ws().readyState !== WebSocket.OPEN) {
      console.warn("openTurnOnce: socket not open");
      return false;
    }
    const reasonLabel = reason || (dbg("audio_safe_mode") ? "safe_mode" : "auto");
    __turnOpen = true;
    __turnOpenAt = Date.now();
    try {
      hubLog("client.turn.intent", { action: "open", reason: reasonLabel });
    } catch {}
    return true;
  }

  function resetTurnIntent(reason) {
    if (__turnOpen) {
      __turnOpen = false;
      __turnOpenAt = 0;
      try {
        hubLog("client.turn.intent", { action: "close", reason: reason || "reset" });
      } catch {}
    }
    if (typeof runtimeResetTurnIntent === "function") {
      return runtimeResetTurnIntent(reason);
    }
    return undefined;
  }

  function canSendInputControl() {
    let phase = null;
    try {
      phase = AppState?.wsPhase || AppState?.connectionState || null;
    } catch {}
    const inReadyPhase = (typeof phase === "string" && phase) ? WS_READY_PHASES.has(phase) : true;
    let connected = false;
    try {
      if (typeof WSClient?.isConnected === "function") {
        connected = WSClient.isConnected();
      } else {
        const live = socket || window.ws;
        connected = !!live && live.readyState === WebSocket.OPEN;
      }
    } catch {}
    return connected && inReadyPhase;
  }

  let __transportMisuseLogging = false;

  function logTransportMisuse(kind) {
    try {
      console.warn("WS misuse:", kind);
    } catch {}
    if (__transportMisuseLogging) {
      return;
    }
    let hub = null;
    try {
      hub = window.AppState?.hub || null;
    } catch {}
    if (!hub || typeof hub.log !== "function") {
      return;
    }
    __transportMisuseLogging = true;
    try {
      // CRITICAL FIX: Decouple the hub log from the synchronous error handling flow
      if (typeof setTimeout === 'function') {
        setTimeout(() => hub.log("client.ws.misuse", { kind }), 0);
      }
    } catch (err) {
      try {
        console.warn("WS misuse hub.log failed", err);
      } catch {}
    } finally {
      __transportMisuseLogging = false;
    }
  }

  const VAD_APPSTATE_KEYS = [
    "vadActive",
    "vadSpeech",
    "vadConfidence",
    "vadEnergyDb",
    "vadNoiseDb",
  ];
  let vadController = null;
  let vadSilenceTimerId = null;
  const senderPauseReasons = new Set();
  let senderPaused = false;
  let warmupUntil = 0;
  function beginWarmup(ms = 1200) {
    warmupUntil = Date.now() + ms;
  }
  function _warming() {
    return Date.now() < warmupUntil;
  }
  function canCaptureNow() {
    if (typeof runtimeCanCaptureNow === "function") {
      return runtimeCanCaptureNow();
    }
    return false;
  }
  function applySenderPausedState() {
    const nextPaused = senderPauseReasons.size > 0;
    if (senderPaused === nextPaused) {
      return;
    }
    senderPaused = nextPaused;
    if (AppState && typeof AppState === "object") {
      AppState.senderPaused = senderPaused;
    }
    updateState({ senderPaused });
    window.requestAnimationFrame(() => window.AppUI?.refresh?.());
    updatePcmSenderState();
  }
  function setSenderPauseReason(reason, value) {
    const key = typeof reason === "string" && reason ? reason : "legacy";
    const desired = Boolean(value);
    if (desired) {
      if (!senderPauseReasons.has(key)) {
        senderPauseReasons.add(key);
        applySenderPausedState();
      }
    } else if (senderPauseReasons.delete(key)) {
      applySenderPausedState();
    }
  }
  function syncSenderPaused(value) {
    setSenderPauseReason("legacy", value);
  }
  let __hubLoggingInFlight = false;

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
        console.warn("AppState.hub.log failed", err);
      } finally {
        __hubLoggingInFlight = false;
      }
    }
    if (typeof window !== "undefined") {
      try {
        window.dispatchEvent(new CustomEvent("client.log", { detail: { label, detail } }));
        return true;
      } catch (err) {
        console.warn("client.log dispatch failed", err);
      }
    }
    return false;
  }

  const audioRuntime = createWsAudioRuntime({
    AppState,
    initPcmSender,
    hubLog,
    updateState,
    logStage,
    getSocket: () => socket,
    WSClient,
    getWsClient: () => WSClient,
    sendAudioChunk: (payload, meta) => {
      if (WSClient && typeof WSClient.sendAudioChunk === "function") {
        return WSClient.sendAudioChunk(payload, meta);
      }
      return false;
    },
    sendJSON: (payload) => {
      if (WSClient && typeof WSClient.sendJSON === "function") {
        WSClient.sendJSON(payload);
        return true;
      }
      return false;
    },
    isAudioStreaming: () => _audioStreaming,
    canCaptureNow: () => canCaptureNow(),
    isSenderPaused: () => senderPaused,
    setSenderPauseReason,
    getVadController: () => vadController,
    getFirstChunkSeen: () => __firstChunkSeen,
    setFirstChunkSeen: (value) => { __firstChunkSeen = Boolean(value); },
    getMicRecordingStartAt: () => __micRecordingStartAt,
    setMicRecordingStartAt: (value) => { __micRecordingStartAt = Number.isFinite(value) ? Number(value) : null; },
    getMicChunks: () => __micChunks,
    setMicChunks: (value) => { __micChunks = Number.isFinite(value) ? Number(value) : 0; },
    getMicBytes: () => __micBytes,
    setMicBytes: (value) => { __micBytes = Number.isFinite(value) ? Number(value) : 0; },
    audioKeepaliveMs: AUDIO_KEEPALIVE_MS,
  });

  const {
    ensurePcmSender,
    handlePcmFrame,
    handlePcmSend,
    handleSampleRate,
    primeAsrStreamFromRing,
    recordRecorderChunk,
    getPcmRing,
    resetSilenceSuppression,
    updatePcmSenderState,
    scheduleAudioKeepalive: runtimeScheduleAudioKeepalive,
    clearAudioKeepaliveTimer: runtimeClearAudioKeepalive,
  } = audioRuntime;

  scheduleAudioKeepaliveImpl = typeof runtimeScheduleAudioKeepalive === "function"
    ? runtimeScheduleAudioKeepalive
    : () => {};
  clearAudioKeepaliveTimerImpl = typeof runtimeClearAudioKeepalive === "function"
    ? runtimeClearAudioKeepalive
    : () => {};

  function cloneClientVadPolicyRoot(root) {
    const safeRoot = root && typeof root === "object" ? root : {};
    const vad = safeRoot && typeof safeRoot.vad === "object" ? safeRoot.vad : {};
    const client = vad && typeof vad.client === "object" ? vad.client : {};
    return { vad: { client: { ...client } } };
  }

  function getVadPolicySnapshot() {
    try {
      const root = getClientVadPolicyRoot();
      if (root && typeof root === "object") {
        return cloneClientVadPolicyRoot(root);
      }
    } catch {}
    return { vad: { client: {} } };
  }

  // Resolve warmup once per session start (policy or default)
  function getWarmupMs() {
    try {
      const snap = getVadPolicySnapshot();
      const ms = snap?.vad?.client?.warmup_ms;
      if (Number.isFinite(ms) && ms >= 0 && ms <= 10000) return ms;
    } catch {}
    try {
      const fallbackRoot = getClientVadPolicyRoot();
      const fallback = fallbackRoot?.vad?.client?.warmup_ms;
      if (Number.isFinite(fallback) && fallback >= 0 && fallback <= 10000) {
        return fallback;
      }
    } catch {}
    return 1200;
  }

  function getTtsActiveSnapshot() {
    try {
      if (typeof AppState.getState === "function") {
        const snapshot = AppState.getState();
        if (snapshot && typeof snapshot.ttsActive === "boolean") {
          return snapshot.ttsActive;
        }
      }
    } catch {}
    return Boolean(AppState?.ttsActive);
  }

  function setVadAppState(patch) {
    if (!patch || typeof patch !== "object") {
      return;
    }
    const sanitized = {};
    for (const key of VAD_APPSTATE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(patch, key)) {
        sanitized[key] = patch[key];
        AppState[key] = patch[key];
      }
    }
    const keys = Object.keys(sanitized);
    if (!keys.length) {
      return;
    }
    updateState(sanitized);
  }

  function resolveConsoleBusFunction() {
    try {
      if (typeof globalThis !== "undefined" && typeof globalThis.consoleBus === "function") {
        return globalThis.consoleBus;
      }
    } catch {}
    if (typeof window !== "undefined" && typeof window.consoleBus === "function") {
      return window.consoleBus;
    }
    return null;
  }

  function emitConsoleBusEvent(event, payload, sampleRate = 1) {
    if (typeof event !== "string" || !event) {
      return;
    }
    const rate = Number.isFinite(sampleRate) && sampleRate > 1 ? Math.floor(sampleRate) : 1;
    if (rate > 1) {
      const bucket = Math.floor(Math.random() * rate);
      if (bucket !== 0) {
        return;
      }
    }
    const bus = resolveConsoleBusFunction();
    if (!bus) {
      return;
    }
    try {
      if (payload === undefined) {
        bus(event);
      } else {
        bus(event, payload);
      }
    } catch (err) {
      try {
        console.warn("consoleBus emit failed", err);
      } catch {}
    }
  }

  function getClientVadPolicyConfig() {
    try {
      const root = getClientVadPolicyRoot();
      const client = root?.vad?.client;
      if (client && typeof client === "object") {
        return client;
      }
    } catch {}
    return {};
  }

  function getVadSilenceTimeoutMs() {
    const config = getClientVadPolicyConfig();
    const candidate = config && Object.prototype.hasOwnProperty.call(config, "max_gate_silence_ms")
      ? Number(config.max_gate_silence_ms)
      : Number(config && config.max_silence_ms);
    if (Number.isFinite(candidate) && candidate > 0) {
      return Math.round(candidate);
    }
    const fallback = Number(MAX_GATE_SILENCE_MS);
    if (Number.isFinite(fallback) && fallback > 0) {
      return Math.round(fallback);
    }
    return null;
  }

  function clearVadSilenceTimer() {
    if (vadSilenceTimerId) {
      clearTimeout(vadSilenceTimerId);
      vadSilenceTimerId = null;
    }
  }

  function scheduleVadSilenceTimer() {
    const timeoutMs = getVadSilenceTimeoutMs();
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return;
    }
    clearVadSilenceTimer();
    vadSilenceTimerId = setTimeout(() => {
      vadSilenceTimerId = null;
      emitConsoleBusEvent("client.vad.silence_timeout", undefined, VAD_SILENCE_TIMEOUT_SAMPLE_RATE);
    }, timeoutMs);
  }

  function publishVad(event, payload) {
    if (typeof event !== "string" || !event) {
      return;
    }
    if (event === "client.vad.speech_start") {
      clearVadSilenceTimer();
      emitConsoleBusEvent("client.vad.start_speech");
      const ring = getPcmRing();
      if (typeof ring?.clear === 'function') {
        try {
          ring.clear();
        } catch (err) {
          try { console.warn("pcmRing.clear failed", err); } catch (_) {}
        }
      }
      schedulePartialWatchdog("vad_speech_start");
    } else if (event === "client.vad.speech_end") {
      const durationValue = Number(payload && payload.duration_ms);
      const durationMs = Number.isFinite(durationValue) ? Math.max(0, Math.round(durationValue)) : null;
      const detail = durationMs !== null ? { duration_ms: durationMs } : undefined;
      emitConsoleBusEvent("client.vad.end_speech", detail);
      scheduleVadSilenceTimer();
      clearPartialWatchdog();
    }
    hubLog(event, payload);
  }

  function handleVadGateChange(action) {
    if (action === "pause") {
      syncSenderPaused(true);
    } else if (action === "resume") {
      syncSenderPaused(false);
    }
    try {
      const state = action;
      hubLog("client.vad.gate", { action, state, senderPaused: AppState?.senderPaused });
    } catch {}
  }

  try {
    vadController = initVAD({
      getPolicy: () => getVadPolicySnapshot(),
      getTtsActive: () => getTtsActiveSnapshot(),
      onGateChange: handleVadGateChange,
      setAppState: (patch) => setVadAppState(patch),
      publish: (event, payload) => publishVad(event, payload),
    });
  } catch (err) {
    try {
      console.warn("VAD initialization failed", err);
    } catch {}
    vadController = null;
  }

  try {
    if (typeof AppState.websocket === "undefined" && typeof AppState.getState === "function") {
      Object.defineProperty(AppState, "websocket", {
        configurable: true, enumerable: false,
        get() { try { return AppState.getState().websocket || null; } catch { return null; } }
      });
    }
  } catch {}

  if (typeof window !== "undefined") {
    try { window.__logMic = logMic; } catch {}
    try { window.__logStage = logStage; } catch {}
    try { window.__MIC_OUTCOME = MIC_OUTCOME; } catch {}
  }

  const AUDIO_HEADER_FRAME = Object.freeze({
    type: "audio.header",
    format: "pcm16",
    sample_rate: 16000,
    channels: 1,
  });
  // --- Begin: header idempotency + strict schema ---
  let __audioHeaderSent = false;
  function __resetAudioHeaderSent() {
    __audioHeaderSent = false;
  }
  function __buildStrictAudioHeader(frameOrPolicy) {
    const base = AUDIO_HEADER_FRAME;
    const source = (frameOrPolicy?.policy || frameOrPolicy) || {};
    const audioPolicy = source && typeof source.audio === "object" ? source.audio : {};
    const formatCandidate = audioPolicy.format ?? base.format;
    const rateCandidate =
      audioPolicy.sample_rate ??
      audioPolicy.rate ??
      base.sample_rate;
    const channelsCandidate = audioPolicy.channels ?? base.channels;

    const normalizedRate = Number(rateCandidate);
    const normalizedChannels = Number(channelsCandidate);

    if (formatCandidate && formatCandidate !== "pcm16") {
      try {
        console.warn(
          "audio.header format policy overridden",
          formatCandidate,
          "→ pcm16"
        );
      } catch {}
    }

    return {
      type: base.type,
      format: "pcm16",
      sample_rate: Number.isFinite(normalizedRate) ? normalizedRate : base.sample_rate,
      channels: Number.isFinite(normalizedChannels) ? normalizedChannels : base.channels,
      codec: "pcm_s16le",
    };
  }
  function __sendAudioHeaderOnce(frameOrPolicy = AppState?.policy) {
    if (__audioHeaderSent) {
      try { console.warn("audio.header already sent; skipping"); } catch {}
      return true;
    }

    let header;
    try {
      header = __buildStrictAudioHeader(frameOrPolicy);
    } catch (err) {
      console.warn("Failed to build audio header", err);
      return false;
    }

    const sendJSON = (WSClient && typeof WSClient.sendJSON === "function")
      ? WSClient.sendJSON.bind(WSClient)
      : null;
    if (!sendJSON) {
      console.warn("Failed to send audio header: WSClient.sendJSON unavailable");
      return false;
    }

    const markSent = () => {
      try { logStage("client.audio_header_send", header); } catch {}
      __audioHeaderSent = true;
      return true;
    };

    try {
      const result = sendJSON(header);
      if (result && typeof result.then === "function") {
        return result.then((ok) => {
          if (ok !== false) {
            return markSent();
          }
          return false;
        }).catch((err) => {
          console.warn("Failed to send audio header", err);
          return false;
        });
      }
      if (result !== false) {
        return markSent();
      }
    } catch (err) {
      console.warn("Failed to send audio header", err);
      return false;
    }

    console.warn("Failed to send audio header: transport returned false");
    return false;
  }
  function sendAudioHeader(frameOrPolicy = AppState?.policy) {
    return __sendAudioHeaderOnce(frameOrPolicy);
  }
  // --- End: header idempotency + strict schema ---
  let __lastErrorSig = null, __lastErrorAt = 0;
  const AUDIO_KEEPALIVE_MS = 4000;

  function normalizeReason(reason) {
    if (typeof reason === "string" && reason) {
      return reason;
    }
    if (reason && typeof reason === "object" && typeof reason.reason === "string" && reason.reason) {
      return reason.reason;
    }
    return "unspecified";
  }

  function setAppStateValue(key, value) {
    if (typeof key !== "string" || !key) {
      return;
    }
    const state = typeof AppState.getState === "function" ? AppState.getState() : null;
    const hasKey = state && Object.prototype.hasOwnProperty.call(state, key);
    const current = hasKey ? state[key] : AppState[key];
    if (Object.is(current, value)) {
      AppState[key] = value;
      return;
    }
    AppState[key] = value;
    updateState({ [key]: value });
  }

  function setListeningState(active) {
    const listening = Boolean(active);
    // Use AppState.setState to update the single source of truth
    updateState({ listening });
    // Clear phase if stopping listening, only if connected
    if (!listening && AppState.wsConnected) {
      setWsPhase("connected");
    }
    updatePcmSenderState();
  }

  function setAsrArmInFlight(inFlight) {
    setAppStateValue("asrArmInFlight", Boolean(inFlight));
  }

  function setWsConnected(connected) {
    setAppStateValue("wsConnected", Boolean(connected));
  }

  function setWsPhase(phase) {
    if (typeof phase !== "string" || !phase) {
      return;
    }
    setAppStateValue("wsPhase", phase);
  }

  function resetRecorderTelemetry() {
    setAppStateValue("chunkCount", 0);
    setAppStateValue("lastChunkTs", null);
    __firstChunkSeen = false;
    __armingGraceUntil = 0;
  }

  async function performStopRecorder(reason) {
    _audioStreaming = false;
    __firstChunkSeen = false;
    __armingGraceUntil = 0;
    const stopReason = normalizeReason(reason);
    resetTurnIntent(stopReason);
    clearAudioKeepaliveTimer();
    clearVadSilenceTimer();
    clearPartialWatchdog();
    resetSilenceSuppression();
    syncSenderPaused(false);
    try {
      const sender = await ensurePcmSender();
      if (sender && typeof sender.setEnabled === "function") {
        try {
          sender.setEnabled(false);
        } catch (err) {
          console.warn("pcm.sender.disable_failed", err);
        }
      }
    } catch (err) {
      console.warn("pcm.sender.disable_failed", err);
    }
    __micRecordingStartAt = null;
    if (vadController && typeof vadController.reset === "function") {
      try {
        vadController.reset();
      } catch (err) {
        try {
          console.warn("VAD reset failed", err);
        } catch {}
      }
    }
    setListeningState(false);
    updatePcmSenderState();
    try {
      hubLog("client.pcm.capture_stop", { reason: stopReason });
    } catch {}
  }

  const USER_INITIATED_STOP_REASONS = new Set([
    "user_requested",
    "user_restart",
    "user_end",
    "client_stop",
    "client_shutdown",
    "resume_invalid",
  ]);

  const SERVER_ERROR_STOP_REASONS = new Set([
    "server_requested",
    "server_error",
    "server_restart",
    "bad_info_frame",
    "bad_info_sequence",
    "resume_invalid",
    "asr_unavailable",
    "tts_start",
    "handshake_close",
    "schema_invalid",
    "bad_utf8",
    "ws_close",
    "client_shutdown",
    "rate_limited",
  ]);

  const SERVER_ERROR_REASON_PATTERNS = [
    /error/,
    /fail/,
    /denied/,
    /timeout/,
    /invalid/,
    /unavailable/,
    /disconnect/,
    /refus/,
    /forbidden/,
    /shutdown/,
  ];

  const VAD_OR_MIC_REASON_PATTERNS = [
    /\bvad(?:[_-]|$)/,
    /\bvoice_activity\b/,
    /\bmic(?:_|-|\s)(?:state|status|pause|paused|mute|muted|off|inactive)/,
  ];

  function toReasonLabel(value) {
    const label = normalizeReason(value);
    return typeof label === "string" ? label : "unspecified";
  }

  function reasonLooksLikeVadOrMic(key) {
    if (!key) {
      return false;
    }
    return VAD_OR_MIC_REASON_PATTERNS.some((pattern) => pattern.test(key));
  }

  function reasonLooksUserInitiated(key) {
    return USER_INITIATED_STOP_REASONS.has(key);
  }

  function reasonLooksServerError(key) {
    if (SERVER_ERROR_STOP_REASONS.has(key)) {
      return true;
    }
    return SERVER_ERROR_REASON_PATTERNS.some((pattern) => pattern.test(key));
  }

  function clearPendingRearm() {
    awaitingTurnEndForRearm = false;
    pendingRearmReason = null;
  }

  function evaluateStopRecorderReason(reason, fallbackReason) {
    const label = toReasonLabel(reason);
    const key = label.trim().toLowerCase();
    if (reasonLooksLikeVadOrMic(key)) {
      return { allowed: false, blocked: true, label };
    }
    if (reasonLooksUserInitiated(key) || reasonLooksServerError(key)) {
      return { allowed: true, blocked: false, label };
    }
    if (fallbackReason) {
      const fallbackLabel = toReasonLabel(fallbackReason);
      const fallbackKey = fallbackLabel.trim().toLowerCase();
      if (!reasonLooksLikeVadOrMic(fallbackKey) && (reasonLooksUserInitiated(fallbackKey) || reasonLooksServerError(fallbackKey))) {
        return { allowed: true, blocked: false, label: fallbackLabel };
      }
    }
    return { allowed: false, blocked: false, label };
  }

  async function stopRecorder(reason, options = {}) {
    const { fallbackReason = null, source = null } = options || {};
    const { allowed, blocked, label } = evaluateStopRecorderReason(reason, fallbackReason);
    if (!allowed) {
      try {
        const meta = { reason: label, source };
        if (blocked) {
          console.info("stopRecorder skipped for VAD/mic trigger", meta);
        } else {
          console.info("stopRecorder skipped for non user/server trigger", meta);
        }
      } catch {}
      return false;
    }
    return performStopRecorder(label);
  }

  async function startRecorderStreaming(policy, reason) {
    // If we're already listening based on the single source of truth, return.
    if (AppState.listening) {
      return true;
    }
    __firstChunkSeen = false;
    clearVadSilenceTimer();
    const captureReason = typeof reason === "string" && reason ? reason : "auto";
    try {
      const sender = await ensurePcmSender();
      if (!sender) {
        console.warn("PCM sender unavailable; cannot start streaming");
        return false;
      }
      resetRecorderTelemetry();
      resetSilenceSuppression();
      syncSenderPaused(false);
      if (vadController && typeof vadController.reset === "function") {
        try {
          vadController.reset();
        } catch (err) {
          try {
            console.warn("VAD reset failed", err);
          } catch {}
        }
      }
      if (typeof sender.resume === "function") {
        await sender.resume();
      }
      __micRecordingStartAt = Date.now();
      _audioStreaming = true;
      updatePcmSenderState();
      scheduleAudioKeepalive();
      // Set the single authoritative state flag:
      setListeningState(true);
      // allow the *first* PCM to pass even if VAD pauses immediately
      __armingGraceUntil = Date.now() + 1200; // ~1.2s
      // audio.header is sent from the ASR-ready handler; do not send another copy here.
      try {
        hubLog("client.pcm.capture_start", { reason: captureReason, policy: !!policy });
      } catch {}
      return true;
    } catch (err) {
      if (err?.name === "NotAllowedError") {
        logStage("client.mic", { outcome: MIC_OUTCOME.ERROR_DENIED, message: err.message || "permission" });
      }
      setListeningState(false);
      _audioStreaming = false;
      throw err;
    }
  }

  function openAsr(opts = {}) {
    if (typeof runtimeOpenAsr === "function") {
      return runtimeOpenAsr(opts);
    }
    return undefined;
  }

  function requestAsrArm(reason) {
    if (typeof runtimeRequestAsrArm === "function") {
      return runtimeRequestAsrArm(reason);
    }
    return undefined;
  }

  async function requestAsrClose(reason = "client_stop") {
    if (typeof runtimeRequestAsrClose === "function") {
      return runtimeRequestAsrClose(reason);
    }
    return undefined;
  }

  async function recoverFromAsrFault(reason) {
    if (typeof runtimeRecoverFromAsrFault === "function") {
      return runtimeRecoverFromAsrFault(reason);
    }
    return undefined;
  }

  async function handleAsrStateFrame(frame) {
    if (typeof runtimeHandleAsrStateFrame === "function") {
      return runtimeHandleAsrStateFrame(frame);
    }
    return undefined;
  }

  // Initialize the client banner state only after related constants are defined.
  ensureClientBannerState();

  const USER_GESTURE_EVENTS = ["pointerdown", "touchstart", "keydown"];

  let gestureListenerCleanup = null;

  function createEventEmitter() {
    const registry = new Map();

    function off(event, handler) {
      if (typeof event !== "string" || !event) {
        return;
      }
      const listeners = registry.get(event);
      if (!listeners || !listeners.size) {
        return;
      }
      if (typeof handler === "function") {
        listeners.delete(handler);
      } else if (handler == null) {
        listeners.clear();
      }
      if (!listeners.size) {
        registry.delete(event);
      }
    }

    function on(event, handler) {
      if (typeof event !== "string" || !event || typeof handler !== "function") {
        return () => {};
      }
      let listeners = registry.get(event);
      if (!listeners) {
        listeners = new Set();
        registry.set(event, listeners);
      }
      listeners.add(handler);
      return () => {
        off(event, handler);
      };
    }

    function emit(event, detail) {
      if (typeof event !== "string" || !event) {
        return;
      }
      const listeners = registry.get(event);
      if (!listeners || !listeners.size) {
        return;
      }
      listeners.forEach((listener) => {
        if (typeof listener !== "function") {
          return;
        }
        try {
          listener(detail);
        } catch (err) {
          console.warn("AppState listener error", err);
        }
      });
    }

    return { on, off, emit };
  }

  function ensureInitialAutostartState() {
    if (!AppState || typeof AppState.getState !== "function") {
      return;
    }
    const snapshot = AppState.getState();
    const patch = {};
    if (typeof snapshot.policy === "undefined") {
      patch.policy = AppState.policy;
    }
    if (typeof snapshot.asrReady === "undefined") {
      patch.asrReady = false;
    }
    if (typeof snapshot.asrVendor === "undefined") {
      patch.asrVendor = null;
    }
    if (typeof snapshot.ttsActive === "undefined") {
      patch.ttsActive = false;
    }
    if (typeof snapshot.asrArmInFlight === "undefined") {
      patch.asrArmInFlight = false;
    }
    if (typeof snapshot.listening === "undefined") {
      patch.listening = false;
    }
    if (typeof snapshot.wsConnected === "undefined") {
      patch.wsConnected = Boolean(AppState.wsConnected);
    }
    if (typeof snapshot.wsPhase === "undefined") {
      patch.wsPhase = typeof AppState.wsPhase === "string" ? AppState.wsPhase : "disconnected";
    }
    if (typeof snapshot.turnState === "undefined") {
      patch.turnState = null;
    }
    // REMOVED: recorder/recorderActive, chunkCount/lastChunkTs/lastErrorCode/lastErrorDetail checks
    if (Object.keys(patch).length) {
      updateState(patch);
    }
    AppState.asrReady = Boolean(snapshot.asrReady);
    AppState.asrVendor = typeof snapshot.asrVendor === 'string' && snapshot.asrVendor
      ? snapshot.asrVendor
      : null;
    AppState.ttsActive = Boolean(snapshot.ttsActive);
    AppState.asrArmInFlight = Boolean(snapshot.asrArmInFlight);
    AppState.listening = Boolean(snapshot.listening);
    AppState.wsConnected = Boolean(snapshot.wsConnected);
    AppState.wsPhase = typeof snapshot.wsPhase === "string" ? snapshot.wsPhase : "disconnected";
    AppState.turnState = typeof snapshot.turnState === "string" ? snapshot.turnState : null;
    // REMOVED: recorder/recorderActive, chunkCount/lastChunkTs/lastErrorCode/lastErrorDetail checks/assignments
  }

  function attachUserGestureListeners() {
    if (!AppState?.policy?.require_user_gesture_first_visit) {
      userGestureSatisfied = true;
      if (typeof gestureListenerCleanup === "function") {
        gestureListenerCleanup();
      }
      return;
    }
    if (userGestureSatisfied || typeof window === "undefined") {
      return;
    }
    if (gestureListenerCleanup) {
      return;
    }
    const listeners = [];
    const handler = (event) => {
      const reason = event && typeof event.type === "string" ? event.type : "gesture";
      markUserGestureSatisfied(reason);
    };
    USER_GESTURE_EVENTS.forEach((eventName) => {
      try {
        window.addEventListener(eventName, handler, { passive: true });
        listeners.push({ eventName, handler });
      } catch (err) {
        console.warn("Failed to attach gesture listener", eventName, err);
      }
    });
    gestureListenerCleanup = () => {
      if (typeof window === "undefined") {
        return;
      }
      listeners.forEach((entry) => {
        window.removeEventListener(entry.eventName, entry.handler);
      });
      gestureListenerCleanup = null;
    };
  }

  function markUserGestureSatisfied(reason) {
    if (userGestureSatisfied) {
      return;
    }
    userGestureSatisfied = true;
    if (typeof gestureListenerCleanup === "function") {
      gestureListenerCleanup();
    }
  }
  function invokeStartRecording(trigger) {
    let handler = null;
    if (typeof window !== "undefined" && typeof window.startRecording === "function") {
      handler = window.startRecording;
    } else if (typeof startRecording === "function") {
      handler = startRecording;
    }
    if (handler) {
      const context = typeof window !== "undefined" ? window : null;
      return handler.call(context, { trigger });
    }
    const hub = AppState?.hub;
    if (hub && typeof hub.startListening === "function") {
      try {
        return hub.startListening({ trigger });
      } catch (err) {
        console.warn("Hub startListening failed", err);
        throw err;
      }
    }
    throw new Error("startRecording_unavailable");
  }
  ensureInitialAutostartState();
  attachUserGestureListeners();

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

  function resolveWsPath() {
    try {
      const routing = AppState?.policy?.routing
        || (FEATURE_LEGACY_POLICY ? AppState?.policy?.policy?.routing : undefined);
      const candidate = typeof routing?.ws_version === 'string' ? routing.ws_version.trim() : '';
      if (candidate && candidate.toLowerCase() !== 'v2') {
        console.warn('Unsupported ws_version from policy; normalizing to v2', candidate);
      }
    } catch (err) {
      console.warn('Failed to inspect policy routing version', err);
    }
    return '/ws/v2/chat';
  }

  function computeUrl(resumeToken) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = `${protocol}//${window.location.host}${resolveWsPath()}`;
    const params = new URLSearchParams();
    if (typeof resumeToken === "string" && resumeToken.trim()) {
      params.set("resume", resumeToken.trim());
    }
    const query = params.toString();
    return query ? `${base}?${query}` : base;
  }

  function sendJson(frame) {
    try {
      if (WSClient && typeof WSClient.sendJSON === "function") {
        return WSClient.sendJSON(frame);
      }
      return connection.send(frame, { binary: false });
    } catch (err) {
      console.error("WSClient sendJson error", err);
      return false;
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

  // ===== Frame dispatchers (ASR, chat, policy, config, etc.) =====
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

  function handleIncomingFrame(frame) {
    return handleMessageFrame(frame);
  }


  function deliverAsr(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }
    if (frame.type === "asr.final" && typeof frame.sid === "string" && frame.sid) {
      if (AppState.asrSid && AppState.asrSid !== frame.sid) {
        console.warn("asr.final sid mismatch", { expected: AppState.asrSid, sid: frame.sid });
      } else {
        AppState.asrSid = frame.sid;
      }
    }
    const view = window.TranscriptView;
    const now = Date.now();
    if (frame.type === "asr.final" && typeof frame.text === "string" && frame.text) {
      const sid = (typeof frame.sid === "string" && frame.sid) || generateProvisionalSid();
      lastUserBySid.set(sid, { text: frame.text, ts: now });
      pruneStaleUserSids(now);
      if (view && typeof view.upsertUser === "function") {
        try {
          view.upsertUser({ key: sid, text: frame.text, provisional: true });
        } catch (err) {
          console.warn("TranscriptView upsertUser error", err);
        }
      }
    } else {
      pruneStaleUserSids(now);
    }
    if (!view) {
      return;
    }
    try {
      if (frame.type === "asr.partial" && typeof view.handlePartial === "function") {
        view.handlePartial(frame);
      } else if (frame.type === "asr.final" && typeof view.handleFinal === "function") {
        view.handleFinal(frame);
      }
    } catch (err) {
      console.warn("TranscriptView ASR handler error", err);
    }
  }

  function deliverChat(frame) {
    if (!frame || typeof frame !== "object") {
      console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_frame" });
      return;
    }
    const view = window.TranscriptView;
    pruneStaleUserSids();
    if (!view || typeof view.handleChatMessage !== "function") {
      queueForTranscript(frame);
      return;
    }

    const turnId = typeof frame.turn_id === "string" ? frame.turn_id : null;
    if (
      turnId &&
      frame.role === "assistant" &&
      assistantStreamingTurns.has(turnId) &&
      typeof view.commitAssistantStreaming === "function"
    ) {
      const record = assistantStreamingTurns.get(turnId) || {};
      const finalText = typeof frame.text === "string" ? frame.text : record.text || "";
      try {
        view.commitAssistantStreaming(turnId, {
          text: finalText,
          messageId: typeof frame.id === "string" ? frame.id : null,
          reqId: typeof frame.req_id === "string" ? frame.req_id : record.reqId || null,
          final: true,
        });
      } catch (err) {
        console.warn("TranscriptView final commit error", err);
      }
      assistantStreamingTurns.delete(turnId);
      return;
    }

    const isUserChat =
      frame.type === "chat.message" &&
      frame.role === "user" &&
      typeof frame.text === "string" &&
      frame.text;

    if (isUserChat) {
      const sid =
        (typeof frame.sid === "string" && frame.sid) ||
        findNearestSid(frame.text);
      if (sid && lastUserBySid.has(sid) && typeof view.upsertUser === "function") {
        try {
          view.upsertUser({ key: sid, text: frame.text, provisional: false });
          lastUserBySid.delete(sid);
          return;
        } catch (err) {
          console.warn("TranscriptView upsertUser error", err);
        }
      }
    }

    try {
      view.handleChatMessage(frame);
    } catch (err) {
      console.warn("TranscriptView chat handler error", err);
    }
  }

  function findNearestSid(text) {
    if (typeof text !== "string" || !text) {
      return null;
    }
    const now = Date.now();
    for (const [sid, record] of lastUserBySid.entries()) {
      if (record && record.text === text && (now - record.ts) < ASR_MATCH_WINDOW_MS) {
        return sid;
      }
    }
    return null;
  }

  window.attachTranscriptView = function attachTranscriptView(view) {
    window.TranscriptView = view;
    if (!view || typeof view.handleChatMessage !== "function") {
      if (pendingTranscriptFrames.length) {
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_transcript_view" });
      }
      return;
    }
    while (pendingTranscriptFrames.length) {
      const frame = pendingTranscriptFrames.shift();
      try {
        deliverChat(frame);
      } catch (err) {
        console.warn("flush chat error", err);
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "transcript_flush_error" });
      }
    }
  };

  function handleChatHistoryFrame(frame) {
    const messages = Array.isArray(frame.messages) ? frame.messages : [];
    if (!messages.length) {
      return;
    }
    for (const message of messages) {
      deliverChat(message);
    }
  }

  async function handleMessageFrame(frame) {
    if (!frame || typeof frame.type !== "string") {
      console.warn("Ignoring WS frame without type", frame);
      return;
    }
    if (frame.type === "keepalive") {
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

    if (typeof WSClient?.emit === "function") {
      try {
        WSClient.emit("frame", frame);
      } catch (err) {
        console.warn("WSClient frame emit failed", err);
      }
    }

    let handledByTranscriptDispatch = false;
    switch (frame.type) {
      case "chat.begin":
        handleAssistantStreamingBegin(frame);
        dispatchFrame(frame);
        return;

      case "chat.delta":
        handleAssistantStreamingDelta(frame);
        dispatchFrame(frame);
        return;

      case "chat.commit":
        handleAssistantStreamingCommit(frame);
        dispatchFrame(frame);
        return;

      case "chat.end":
        handleAssistantStreamingEnd(frame);
        dispatchFrame(frame);
        return;

      case "chat.message":
      case "message":
        deliverChat(frame);
        dispatchFrame(frame);
        return;

      case "asr.partial":
        schedulePartialWatchdog("asr.partial");
        if (transcriptFrameAllowed(frame)) {
          deliverAsr(frame);
        } else {
          logStage("ui_transcript_filter", { allow: false, type: frame.type });
        }
        handledByTranscriptDispatch = true;
        break;

      case "asr.final":
        clearPartialWatchdog();
        if (transcriptFrameAllowed(frame)) {
          deliverAsr(frame);
        } else {
          logStage("ui_transcript_filter", { allow: false, type: frame.type });
        }
        handledByTranscriptDispatch = true;
        break;

      default:
        break;
    }

    if (handledByTranscriptDispatch) {
      dispatchFrame(frame);
      return;
    }

    const delegateToTurnRuntime = (
      typeof frame.type === "string" &&
      ((frame.type.startsWith("asr.") && frame.type !== "asr.partial" && frame.type !== "asr.final") ||
        frame.type === "turn.begin" ||
        frame.type === "turn.end")
    );

    if (delegateToTurnRuntime) {
      await handleAsrStateFrame(frame);
      const dispatchable = frame.type === "asr.ready"
        ? sanitizeAsrReadyFrame(frame)
        : frame;
      dispatchFrame(dispatchable);
      return;
    }

    if (frame.type === "audio.throttle") {
      const rawMs = Number(frame?.ms);
      const ms = Number.isFinite(rawMs) ? Math.max(0, rawMs) : 0;
      const now = Date.now();
      const until = ms > 0 ? now + ms : now;
      __pauseSendUntil = ms > 0 ? Math.max(__pauseSendUntil, until) : now;
      if (AppState && typeof AppState === "object") {
        AppState._throttleUntil = __pauseSendUntil;
      }
      try {
        hubLog("client.audio.throttle", { ms, until: __pauseSendUntil });
      } catch {}
      if (__throttleTimer) {
        clearTimeout(__throttleTimer);
        __throttleTimer = null;
      }
      if (ms > 0) {
        const delay = Math.max(0, __pauseSendUntil - Date.now());
        __throttleTimer = setTimeout(() => {
          __throttleTimer = null;
          if (AppState && typeof AppState === "object") {
            AppState._throttleUntil = 0;
          }
          try { AppState?.hub?.log?.('client.audio.resume_after_throttle', { at: Date.now() }); } catch {}
        }, delay);
      } else {
        try { AppState?.hub?.log?.('client.audio.resume_after_throttle', { at: Date.now() }); } catch {}
        if (AppState && typeof AppState === "object") {
          AppState._throttleUntil = 0;
        }
      }
      return;
    }

    if (frame.type === "config.updated" || frame.type === "config_updated") {
      const sourcePolicy = frame && typeof frame === 'object' ? frame.policy : null;
      const appliedPolicy = applyPolicySnapshotFromSource(sourcePolicy, 'config.updated');
      const incomingType = typeof frame.type === 'string' ? frame.type : 'config.updated';
      if (incomingType !== 'config.updated') {
        dispatchFrame({ ...frame, policy: appliedPolicy, type: incomingType });
      }
      return;
    }

    if (frame.type === "policy.interaction") {
      const sanitized = policyRuntime.sanitizePolicyFrame(frame);
      const appliedPolicy = applyPolicySnapshotFromSource(
        sanitized && sanitized.policy ? sanitized.policy : null,
        'policy.interaction'
      );
      sanitized.policy = appliedPolicy;
      if (!userGestureSatisfied && !appliedPolicy.require_user_gesture_first_visit) {
        markUserGestureSatisfied('policy_update');
      }
      attachUserGestureListeners();
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
        await handleErrorFrame(frame);
        return;
      } else if (frame.type !== "info") {
        console.error("Expected info frame first, received", frame.type);
        await close("bad_info_sequence");
        return;
      }
      if (frame.type === "info") {
        await handleInfoFrame(frame);
      }
    } else if (frame.type === "info") {
      await handleInfoFrame(frame);
    } else if (frame.type === "server.pong") {
      handlePongFrame(frame);
    } else if (frame.type === "pong") {
      handlePongFrame(frame);
    } else if (frame.type === "error") {
      await handleErrorFrame(frame);
    } else if (frame.type === "tts.start") {
      try {
        AppState?.hub?.stopListening?.("tts");
      } catch {}
      await stopRecorder("tts_start");
      setAppStateValue("ttsActive", true);
      AppState.tts = true;
      window.requestAnimationFrame(() => window.AppUI?.refresh?.());
      if (typeof AppState.emit === "function") {
        AppState.emit("ttsActive", { active: true });
      }
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.handleTtsStart === "function") {
        audioPlayer.handleTtsStart(frame);
      }
      const uttIdStart = frame?.utt_id || 'utt-00001';
      logStage('client.tts_start', { utt_id: uttIdStart });
      logStage('client.tts', { outcome: 'playing', utt_id: uttIdStart });
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: 'tts' });
    } else if (frame.type === "tts.end") {
      setAppStateValue("ttsActive", false);
      try {
        window.dispatchEvent(new CustomEvent("tts.end", { detail: frame }));
      } catch {}
      AppState.tts = false;
      beginWarmup(getWarmupMs());
      window.requestAnimationFrame(() => window.AppUI?.refresh?.());
      if (typeof AppState.emit === "function") {
        AppState.emit("ttsActive", { active: false });
      }
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.handleTtsEnd === "function") {
        audioPlayer.handleTtsEnd(frame);
      }
      const uttIdEnd = frame?.utt_id || 'utt-00001';
      logStage('client.tts_end', { utt_id: uttIdEnd, dur_ms: frame?.dur_ms });
      logStage('client.tts', { outcome: 'ended', utt_id: uttIdEnd, dur_ms: frame?.dur_ms });

      // *** NEW STABLE LOGIC: Arm ASR immediately after TTS ends (zero delay) ***
      // We rely on the server to send asr.ready back when it processes this.
      if (AppState?.policy?.auto_record_after_greet !== false) {
        requestAsrArm('tts_end');
      }
      // *** END NEW LOGIC ***
    } else if (frame.type === "tts.cancel" || frame.type === "tts.error") {
      const uttId = frame?.utt_id || 'utt-00001';
      const reason = frame.type === "tts.cancel" ? 'cancel' : 'error';
      setAppStateValue("ttsActive", false);
      logStage('client.tts', {
        outcome: 'ended',
        utt_id: uttId,
        dur_ms: frame?.dur_ms,
        reason,
      });
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: `tts_${reason}` });
    } else if (frame.type === "start_listening") {
      const policy = frame?.policy || {};
      if (!AppState?.asrReady) {
        console.warn("Received start_listening before ASR ready; ignoring until asr.ready arrives.", frame);
        return;
      }
      console.info("start_listening received after ASR ready; relying on automatic mic start.", {
        vendor: policy?.asr?.vendor?.primary ?? null,
      });
      return;
    } else if (frame.type === "stop_listening") {
      const rawStopReason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "stop_listening";
      if (_audioStreaming) {
        hubLog("client.stream.off", { reason: rawStopReason });
      }
      _audioStreaming = false;
      await stopRecorder({ reason: rawStopReason }, { fallbackReason: "server_requested", source: "server.stop_listening" });
      setAsrArmInFlight(false);
      try {
        const hub = AppState?.hub;
        if (hub && typeof hub.stopListening === "function") {
          hub.stopListening("server_requested");
        } else {
          stopInputCapture({ reason: "server_requested" });
        }
        logMic({ outcome: MIC_OUTCOME.STOPPED, reason: "server_requested" });
      } catch (err) {
        console.warn("Hub stop_listening handler error", err);
        logMic({ outcome: MIC_OUTCOME.ERROR_STATE_GUARD, message: err?.message });
      }
      if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        try {
          const reason =
            (typeof frame?.reason === "string" && frame.reason) ||
            "server_requested";
          window.dispatchEvent(new CustomEvent("stop_listening", { detail: { reason } }));
        } catch (err) {
          console.warn("stop_listening event dispatch failed", err);
        }
      }
    } else if (frame.type === "input.start") {
      _audioStreaming = true;
      setListeningState(true);
      emitConsoleBusEvent("client.ui_badge", { state: "Listening" });
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.start";
      __turnOpen = true;
      __turnOpenAt = Date.now();
      hubLog("client.stream.on", { reason });
      // NEW: Rely on input.start to open turn, but mic start is tied to ASR readiness
      await openTurnOnce(reason); 
      await handleInputStartFrame(frame);
    } else if (frame.type === "input.stop") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.stop";
      if (_audioStreaming) {
        hubLog("client.stream.off", { reason });
      }
      _audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
      emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      stopInputCapture({ reason: "input.stop" });
      __resetAudioHeaderSent();
    } else if (frame.type === "assistant.await_user") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "assistant.await_user";
      if (_audioStreaming) {
        hubLog("client.stream.off", { reason });
      }
      _audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
    } else if (frame.type === "chat.history") {
      handleChatHistoryFrame(frame);
    }
    dispatchFrame(frame);
  }

  const connection = createWsConnection({
    AppState,
    eventEmitter: WSClient.__events || null,
    telemetry: {
      logStage,
      recordClientBannerEvent,
      recordLastError,
    },
    policyRuntime,
    audioRuntime,
    hubLog,
    handleIncomingFrame,
  });

  const turnRuntime = createTurnRuntime({
    AppState,
    policyRuntime,
    audioRuntime,
    connection,
    telemetry: {
      logStage,
      recordClientBannerEvent,
    },
    hubLog,
  });

  ({
    resetTurnIntent: runtimeResetTurnIntent,
    canCaptureNow: runtimeCanCaptureNow,
    openAsr: runtimeOpenAsr,
    requestAsrArm: runtimeRequestAsrArm,
    requestAsrClose: runtimeRequestAsrClose,
    recoverFromAsrFault: runtimeRecoverFromAsrFault,
    handleAsrStateFrame: runtimeHandleAsrStateFrame,
  } = turnRuntime);

  WSClient.on("open", (event) => {
    const ws = event && typeof event === "object" ? event.websocket || null : null;
    socket = ws || null;
    try { WSClient._ws = ws || null; } catch {}
    if (ws) {
      const protocol = typeof ws.protocol === "string" && ws.protocol ? truncateBannerString(ws.protocol, 48) : null;
      recordClientBannerEvent("ws.socket.open", protocol ? { protocol } : null);
    }
    flushClientBannerQueue();
  });

  WSClient.on("close", (event) => {
    socket = null;
    try { WSClient._ws = null; } catch {}
    const detailReason = event && typeof event === "object" && typeof event.reason === "string" && event.reason
      ? event.reason
      : null;
    const closeCode = event && typeof event.code === "number" ? event.code : undefined;
    recordClientBannerEvent("ws.socket.close", {
      code: closeCode,
      reason: truncateBannerString(detailReason || "", 160),
      was_clean: Boolean(event?.wasClean),
      ready_state: typeof event?.target?.readyState === "number" ? event.target.readyState : undefined,
    });
    if (detailReason) {
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: detailReason });
    }
  });

  function normalizeIncomingFrame(frame) {
    if (!frame || typeof frame !== "object") {
      return null;
    }
    if (typeof frame.type === "string" && frame.type) {
      return frame;
    }
    let inferredType = null;
    if (typeof frame.kind === "string" && frame.kind) {
      inferredType = frame.kind;
    } else if (typeof frame.event === "string" && frame.event) {
      inferredType = frame.event;
    } else if (
      typeof frame.code === "string" ||
      typeof frame.detail === "string" ||
      typeof frame.message === "string"
    ) {
      inferredType = "error";
    }
    if (!inferredType) {
      return null;
    }
    return { ...frame, type: inferredType };
  }

  async function processControlFrameObject(frame) {
    if (frame && typeof frame.message === "string") {
      const normalizedType =
        (typeof frame.type === "string" && frame.type) ||
        (typeof frame.kind === "string" && frame.kind) ||
        (typeof frame.event === "string" && frame.event) ||
        null;
      if (normalizedType !== "chat.message") {
        if (IGNORED_VENDOR_MESSAGES.has(frame.message)) {
          return;
        }
      } else if (IGNORED_VENDOR_MESSAGES.has(frame.message)) {
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "filtered" });
      }
    }
    const normalizedFrame = normalizeIncomingFrame(frame);
    if (!normalizedFrame) {
      console.warn("Dropping WS frame without recognizable type", frame);
      await handleErrorFrame({
        type: "error",
        code: typeof frame?.code === "string" ? frame.code : "schema_invalid",
        detail:
          typeof frame?.detail === "string"
            ? frame.detail
            : "Frame missing type field",
      });
      return;
    }
    if (normalizedFrame.type === "server.ping") {
      connection.send({ type: "client.pong", ts: Date.now(), echo: normalizedFrame.ts });
      return;
    }
    await handleMessageFrame(normalizedFrame);
  }

  async function parseFrame(event) {
    try {
      const { data } = event;
      if (typeof data === "string") {
        try {
          const frame = JSON.parse(data);
          await processControlFrameObject(frame);
        } catch (err) {
          console.error("Failed to parse WS frame", err, data);
        }
        return;
      }
      if (data instanceof Blob) {
        if (getNegotiatedControlCodec() === "msgpack") {
          try {
            const buffer = await data.arrayBuffer();
            const frame = tryDecodeMsgpackFrame(buffer);
            if (frame) {
              await processControlFrameObject(frame);
              return;
            }
          } catch (err) {
            console.warn("Failed to decode msgpack blob", err);
          }
        }
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
        if (getNegotiatedControlCodec() === "msgpack") {
          const frame = tryDecodeMsgpackFrame(chunk);
          if (frame) {
            await processControlFrameObject(frame);
            return;
          }
        }
        const audioPlayer = getAudioPlayer();
        if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
          audioPlayer.enqueueChunk(chunk);
        }
        window.dispatchEvent(new CustomEvent("binary", { detail: chunk }));
        return;
      }
      console.warn("Unknown WS frame type", data);
    } catch (outerErr) {
      console.error("Uncaught exception in parseFrame", outerErr);
      hubLog("client.ws.parse_crash", { error: outerErr?.message, frame_data: event?.data });
    }
  }



  function startInputCapture(frame) {
    const policy = frame?.policy || {};
    const hasPolicy = policy && typeof policy === "object" && Object.keys(policy).length > 0;
    const source = frame?.type || "input.start";
    const unifiedRecorder = window?.AudioRecorder && typeof window.AudioRecorder.startListening === "function";

    if (unifiedRecorder) {
      try {
        logStage("client.input.capture", { source, hasPolicy, skipped: "unified_recorder" });
      } catch {}
      // In unified mode we don't start here; asr.ready will trigger streaming.
      return;
    }

    const hub = AppState?.hub;
    if (hub && typeof hub.startListening === "function") {
      try {
        try {
          logStage("client.input.capture", { source, hasPolicy });
        } catch {}
        return hub.startListening(policy);
      } catch (err) {
        console.warn("Hub startListening (legacy input) failed", err);
      }
    }
    console.warn("Legacy input capture is disabled; recorder hub missing.", frame);
  }

  function stopInputCapture(options = {}) {
    const hub = AppState?.hub;
    if (hub && typeof hub.stopListening === "function") {
      try {
        const reason =
          options && typeof options === "object" && options.reason ? options.reason : "legacy_input";
        hub.stopListening(reason);
      } catch (err) {
        console.warn("Hub stopListening (legacy input) failed", err);
      }
      return;
    }
    void options;
  }

  async function handleInputStartFrame(frame) {
    // REMOVED: All complex logic for pending start and asrReady check
    // The mic start logic is now centralized in the ASR.ready handler.
    
    // We only call openTurnOnce here to ensure the turn is registered early
    const asrReady = Boolean(AppState?.asrReady);
    if (asrReady) {
        try {
          await startRecorderStreaming(frame?.policy || {}, "input.start_asr_ready");
        } catch (err) {
          console.error("input.start deferred start failed", err);
        }
    }
  }

  function handleInputStopFrame() {
    stopInputCapture({ reason: 'input.stop' });
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

  function sanitizeAsrReadyFrame(frame) {
    const safe = { type: "asr.ready", vendor: DEFAULT_ASR_VENDOR };
    if (!frame || typeof frame !== "object") {
      return safe;
    }

    if (typeof frame.sid === "string" && frame.sid) {
      safe.sid = frame.sid;
    }

    if (typeof frame.vendor === "string" && frame.vendor) {
      const normalized = frame.vendor.trim().toLowerCase();
      if (normalized === DEFAULT_ASR_VENDOR) {
        safe.vendor = DEFAULT_ASR_VENDOR;
      }
    }

    const rawInput = frame.input && typeof frame.input === "object" ? frame.input : null;
    const input = {};
    let hasInputField = false;

    if (rawInput) {
      if (typeof rawInput.container === "string" && rawInput.container) {
        input.container = rawInput.container;
        hasInputField = true;
      }
      if (typeof rawInput.codec === "string" && rawInput.codec) {
        input.codec = rawInput.codec;
        hasInputField = true;
      }
      if (typeof rawInput.mode === "string" && rawInput.mode) {
        input.mode = rawInput.mode;
        hasInputField = true;
      }
      if (typeof rawInput.mime === "string" && rawInput.mime) {
        input.mime = rawInput.mime;
        hasInputField = true;
      }

      if (Number.isFinite(rawInput.rate_hz)) {
        input.rate_hz = rawInput.rate_hz;
        hasInputField = true;
      } else if (typeof rawInput.rate_hz === "string" && rawInput.rate_hz.trim()) {
        const parsedRate = Number(rawInput.rate_hz);
        if (Number.isFinite(parsedRate)) {
          input.rate_hz = parsedRate;
          hasInputField = true;
        }
      }

      if (Number.isFinite(rawInput.channels)) {
        input.channels = rawInput.channels;
        hasInputField = true;
      } else if (typeof rawInput.channels === "string" && rawInput.channels.trim()) {
        const parsedChannels = Number(rawInput.channels);
        if (Number.isFinite(parsedChannels)) {
          input.channels = parsedChannels;
          hasInputField = true;
        }
      }
    }

    const captureSources = [];
    if (rawInput && rawInput.capture && typeof rawInput.capture === "object") {
      captureSources.push(rawInput.capture);
    }
    if (frame.capture && typeof frame.capture === "object") {
      captureSources.push(frame.capture);
    }
    for (const capture of captureSources) {
      if (!capture || typeof capture !== "object") continue;
      if (Number.isFinite(capture.timeslice_ms)) {
        input.timeslice_ms = capture.timeslice_ms;
        hasInputField = true;
        break;
      }
      if (typeof capture.timeslice_ms === "string" && capture.timeslice_ms.trim()) {
        const parsedSlice = Number(capture.timeslice_ms);
        if (Number.isFinite(parsedSlice)) {
          input.timeslice_ms = parsedSlice;
          hasInputField = true;
          break;
        }
      }
    }

    if (!hasInputField) {
      return safe;
    }

    safe.input = input;
    return safe;
  }

  function handleServerBannerFrame(frame) {
    const sanitized = sanitizeServerBannerFrame(frame);
    updateState({ serverBanner: sanitized });
    console.log("WS server banner", sanitized);
    dispatchFrame(sanitized);
  }

  function handleAsrReadyFrame(frame) {
    const sanitized = sanitizeAsrReadyFrame(frame);
    if (typeof sanitized.sid === "string" && sanitized.sid) {
      AppState.asrSid = sanitized.sid;
    } else if (frame && typeof frame.sid === "string" && frame.sid) {
      AppState.asrSid = frame.sid;
    }
    AppState.asrReady = true;
    try {
      window.dispatchEvent(new CustomEvent("asr.ready"));
    } catch {}
    AppState.asrVendor = sanitized.vendor || DEFAULT_ASR_VENDOR;
    beginWarmup(getWarmupMs());
    updateState({ asrReady: true, asrVendor: AppState.asrVendor });
    updatePcmSenderState();
    window.requestAnimationFrame(() => window.AppUI?.refresh?.());
    if (typeof AppState.emit === "function") {
      AppState.emit("asrReady", {
        ready: true,
        vendor: AppState.asrVendor,
        input: sanitized.input ?? null,
      });
    }
    logStage('client.asr', {
      stage: 'ready',
      vendor: AppState.asrVendor,
      input: sanitized.input ?? null,
    });
    return sanitized;
  }

  async function handleInfoFrame(frame) {
    const meta = frame && frame.meta;
    if (!meta || typeof meta.sid !== "string") {
      console.error("Invalid info frame", frame);
      await close("bad_info_frame");
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
    if (frame && typeof frame.policy === 'object') {
      applyPolicySnapshotFromSource(frame.policy, 'info');
    }
    logStage('client.ws', { outcome: 'auth_ok' });
    flushClientBannerQueue();
  }

  function handlePongFrame(frame) {
    const now = Date.now();
    let reference = null;
    if (frame && typeof frame.echo === "number") {
      reference = frame.echo;
    } else if (frame && typeof frame.t === "number") {
      reference = frame.t;
    } else {
      try {
        const snapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
        const statePing = snapshot && typeof snapshot.lastPingAt === "number" ? snapshot.lastPingAt : null;
        if (Number.isFinite(statePing)) {
          reference = statePing;
        }
      } catch {}
    }
    let rtt = null;
    if (typeof reference === "number") {
      const latency = Math.max(0, now - reference);
      updateState({ latencyMs: latency });
      rtt = latency;
    }
    logStage('client.ws', { outcome: 'pong', rtt_ms: rtt });
  }

  const pendingTranscriptFrames = [];
  const assistantStreamingTurns = new Map();
  const ASR_MATCH_WINDOW_MS = 4000;
  const lastUserBySid = new Map();
  let provisionalSidCounter = 0;

  function generateProvisionalSid() {
    provisionalSidCounter = (provisionalSidCounter + 1) % Number.MAX_SAFE_INTEGER;
    return `sid:${Date.now()}:${provisionalSidCounter}`;
  }

  function pruneStaleUserSids(now = Date.now()) {
    for (const [sid, record] of lastUserBySid.entries()) {
      if (!record || typeof record.ts !== "number" || (now - record.ts) > ASR_MATCH_WINDOW_MS) {
        lastUserBySid.delete(sid);
      }
    }
  }

  function handleAssistantStreamingBegin(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    let record = assistantStreamingTurns.get(turnId);
    if (!record) {
      record = { text: "", totalLen: 0, committed: false, ended: false, reqId: null };
      assistantStreamingTurns.set(turnId, record);
    }
    if (typeof frame?.req_id === "string" && frame.req_id) {
      record.reqId = frame.req_id;
    }
    const view = window.TranscriptView;
    if (view && typeof view.beginAssistantStreaming === "function") {
      try {
        view.beginAssistantStreaming({ turnId, reqId: record.reqId || null });
      } catch (err) {
        console.warn("TranscriptView beginAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingDelta(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    if (!assistantStreamingTurns.has(turnId)) {
      handleAssistantStreamingBegin(frame);
    }
    const record = assistantStreamingTurns.get(turnId);
    if (!record) return;
    const append = typeof frame?.append === "string" ? frame.append : "";
    if (typeof frame?.req_id === "string" && frame.req_id) {
      record.reqId = frame.req_id;
    }
    if (append) {
      record.text = `${record.text || ""}${append}`;
    }
    if (typeof frame?.total_len === "number") {
      record.totalLen = frame.total_len;
    } else if (append) {
      record.totalLen = (record.totalLen || 0) + append.length;
    }
    const view = window.TranscriptView;
    if (append && view && typeof view.appendAssistantStreaming === "function") {
      try {
        view.appendAssistantStreaming(turnId, append);
      } catch (err) {
        console.warn("TranscriptView appendAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingCommit(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    if (!assistantStreamingTurns.has(turnId)) {
      handleAssistantStreamingBegin(frame);
    }
    const record = assistantStreamingTurns.get(turnId);
    if (!record) return;
    if (typeof frame?.total_len === "number") {
      record.totalLen = frame.total_len;
    }
    if (typeof frame?.text === "string") {
      record.text = frame.text;
    }
    record.committed = true;
    const view = window.TranscriptView;
    if (view && typeof view.commitAssistantStreaming === "function") {
      try {
        view.commitAssistantStreaming(turnId, {
          text: record.text,
          reqId: record.reqId || null,
        });
      } catch (err) {
        console.warn("TranscriptView commitAssistantStreaming error", err);
      }
    }
  }

  function handleAssistantStreamingEnd(frame) {
    const turnId = typeof frame?.id === "string" ? frame.id : null;
    if (!turnId) return;
    const record = assistantStreamingTurns.get(turnId);
    if (!record) {
      return;
    }
    record.ended = true;
    const view = window.TranscriptView;
    if (view && typeof view.commitAssistantStreaming === "function") {
      try {
        view.commitAssistantStreaming(turnId, {
          text: record.text,
          reqId: record.reqId || null,
        });
      } catch (err) {
        console.warn("TranscriptView commitAssistantStreaming error", err);
      }
    }
  }

  function queueForTranscript(frame) {
    pendingTranscriptFrames.push(frame);
  }

  function transcriptFrameAllowed(frame) {
    const type = typeof frame?.type === "string" ? frame.type : "";
    const role = typeof frame?.role === "string" ? frame.role : "";
    const allow = type === "asr.partial" || type === "asr.final";
    try {
      console.log(`evt=ui_transcript_filter allow=${allow} type=${type || ""} role=${role || ""}`);
    } catch {}
    return allow;
  }


  function findNearestSid(text) {
    if (typeof text !== "string" || !text) {
      return null;
    }
    const now = Date.now();
    for (const [sid, record] of lastUserBySid.entries()) {
      if (record && record.text === text && (now - record.ts) < ASR_MATCH_WINDOW_MS) {
        return sid;
      }
    }
    return null;
  }

  window.attachTranscriptView = function attachTranscriptView(view) {
    window.TranscriptView = view;
    if (!view || typeof view.handleChatMessage !== "function") {
      if (pendingTranscriptFrames.length) {
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "invalid_transcript_view" });
      }
      return;
    }
    while (pendingTranscriptFrames.length) {
      const frame = pendingTranscriptFrames.shift();
      try {
        deliverChat(frame);
      } catch (err) {
        console.warn("flush chat error", err);
        console.warn("chat.message dropped", { phase: AppState?.wsPhase, reason: "transcript_flush_error" });
      }
    }
  };


  async function handleErrorFrame(frame) {
    const code = typeof frame?.code === "string" ? frame.code : "unknown";
    const sig = `${code}|${frame?.detail || frame?.message || ""}`;
    const now = Date.now();
    if (__lastErrorSig === sig && (now - __lastErrorAt) < 1500) {
      // Avoid console spam for the same error flooding in
    } else {
      console.error("WS error frame", frame);
      __lastErrorSig = sig;
      __lastErrorAt = now;
    }
    if (code === "schema_invalid" || code === "bad_utf8") {
      try { stopInputCapture({ reason: code }); } catch {}
      try { clearAudioKeepaliveTimer(); } catch {}
      try { setAsrArmInFlight(false); } catch {}
      __resetAudioHeaderSent();
    }
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
    try {
      const reason = typeof frame?.detail === "string" && frame.detail
        ? frame.detail
        : (typeof frame?.message === "string" ? frame.message : null);
      logStage('client.ws', {
        outcome: 'auth_fail',
        code: typeof frame?.code === 'string' ? frame.code : null,
        reason: reason || null,
      });
    } catch {}
    if (isResumeInvalid) {
      await close("resume_invalid");
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
    socket = ws;
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

    if (_audioStreaming) {
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
    const normalizedReason = toReasonLabel(reason || DEFAULT_CLOSE_REASON);
    const reasonKey = normalizedReason.trim().toLowerCase();
    if (reasonLooksLikeVadOrMic(reasonKey) && !(reasonLooksUserInitiated(reasonKey) || reasonLooksServerError(reasonKey))) {
      try {
        console.info("WSClient.close ignored for VAD/mic trigger", { reason: normalizedReason });
      } catch {}
      return;
    }
    const closeReason = normalizedReason || DEFAULT_CLOSE_REASON;
    if (_audioStreaming) {
      const offReason = closeReason || "client_shutdown";
      hubLog("client.stream.off", { reason: offReason });
    }
    _audioStreaming = false;
    recordClientBannerEvent("ws.close.request", { reason: truncateBannerString(closeReason || "", 80) });
    // Reset header state so the next session emits header again
    try { typeof __resetAudioHeaderSent === 'function' && __resetAudioHeaderSent(); } catch {}
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
    socket = null;
    try { WSClient._ws = null; } catch {}
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    autoResumeAttemptToken = null;
    emitResumeInvalid();
    updateState({ connectionState: "disconnected", websocket: null, infoFrame: null, serverBanner: null });
  }

  function isTypedObjectPayload(payload) {
    if (!payload || typeof payload !== "object") {
      return false;
    }
    if (payload instanceof Blob || payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
      return false;
    }
    return true;
  }

  const BINARY_JSON_GUARD_MAX_BYTES = 512;

  function extractArrayBuffer(payload) {
    if (payload instanceof ArrayBuffer) {
      return payload;
    }
    if (ArrayBuffer.isView(payload)) {
      try {
        return payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
      } catch (err) {
        try { console.warn("WSClient binary guard: buffer slice failed", err); } catch (_) {}
        return null;
      }
    }
    return null;
  }

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
      try { console.warn("WSClient binary guard: decode failed", err); } catch (_) {}
      return null;
    }
  }

  function handleBinaryJsonPayload(payload, { source = "wsclient.binary" } = {}) {
    const decoded = decodeBinaryJsonCandidate(payload);
    if (!decoded) {
      return null;
    }
    const candidate = decoded.parsed;
    if (!validateOutboundPayload(candidate, { rawPayload: decoded.text, source })) {
      return false;
    }
    logTransportMisuse("binary_json_payload");
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "binary_json_payload",
        source,
      });
    } catch (_) {}
    return candidate;
  }

  const debug = {
    simulateIncomingFrame(frame) {
      return handleMessageFrame(frame);
    }
  };

  // ===== Public WSClient API wiring (window.WSClient = …) =====
  WSClient.sendJSON = function sendJSONPayload(obj) {
    if (obj instanceof ArrayBuffer || ArrayBuffer.isView(obj)) {
      logTransportMisuse("binary_sent_to_sendJSON");
      console.error("WSClient.sendJSON: binary payload; use sendAudioChunk()");
      return false;
    }
    if (!obj || typeof obj !== "object") {
      return false;
    }
    const result = connection.send(obj, { binary: false });
    if (result && typeof result.then === "function") {
      return result.then(() => true).catch((err) => {
        console.warn("sendJSON failed", err);
        return false;
      });
    }
    return result !== false;
  };

  WSClient.sendAudioChunk = function sendAudioChunk(buf, opts = {}) {
    if (buf instanceof Blob) {
      const blobResult = connection.sendBinary(buf, { ...opts, lane: opts && typeof opts === "object" && typeof opts.lane !== "undefined" ? opts.lane : "mic" });
      if (blobResult && typeof blobResult.then === "function") {
        return blobResult;
      }
      return blobResult !== false;
    }
    if (!(buf instanceof ArrayBuffer) && !isTypedArray(buf)) {
      const coerced = toArrayBuffer(buf);
      if (!coerced) {
        logTransportMisuse("sendAudioChunk_invalid_payload");
        console.error("sendAudioChunk: expected ArrayBuffer or TypedArray");
        return false;
      }
      buf = coerced;
    }
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (typeof options.lane === "undefined") {
      options.lane = "mic";
    }
    const lane = typeof options.lane === "string" ? options.lane : "mic";
    if (lane === "mic") {
      const now = Date.now();
      if (now < __pauseSendUntil) {
        const pauseMs = __pauseSendUntil - now;
        const ts = Number.isFinite(options.ts) ? Number(options.ts) : now;
        try { AppState?.hub?.log?.('client.audio.chunk_dropped_throttle', { ts, pause_ms: pauseMs }); } catch {}
        return true;
      }
    }
    const result = connection.sendBinary(buf, options);
    if (result && typeof result.then === "function") {
      return result;
    }
    return result !== false;
  };

  WSClient.open = function openWs(options = {}, protocolsOverride) {
    return open(options, protocolsOverride);
  };
  WSClient.close = function closeWs(reason) {
    return close(reason);
  };
  WSClient.send = function sendWs(payload, opts = {}) {
    const options = opts && typeof opts === "object" ? { ...opts, binary: false } : { binary: false };
    return connection.send(payload, options);
  };

  // Fast-path for callers that use sendJSON() directly (audio.header, pings, etc.).
  (function wrapSendJSON() {
    const __origSendJSON = WSClient.sendJSON;
    WSClient.sendJSON = function sendJSONFast(frame) {
      try {
        const ws = socket || window.ws;
        const open = ws && ws.readyState === WebSocket.OPEN;
        const isControl = isControlFrame(frame);
        if (open && isControl) {
          const codec = getNegotiatedControlCodec();
          const encoded = encodeControlFramePayload(frame, codec);
          if (!encoded) {
            return false;
          }
          try {
            ws.send(encoded.payload);
            return true;
          } catch (e) {
            console.warn("ws.json send failed", e);
            return false;
          }
        }
      } catch {}
      return __origSendJSON.call(WSClient, frame);
    };
  })();
  WSClient.sendBinary = (payload, opts = {}) => connection.sendBinary(payload, opts);
  WSClient.getBufferedAmount = () => connection.getBufferedAmount();
  WSClient.requestAsrArm = function wsClientRequestAsrArm(reason) {
    return requestAsrArm(reason);
  };
  WSClient.openAsr = function wsClientOpenAsr(opts = {}) {
    return openAsr(opts);
  };
  WSClient.requestAsrClose = function wsClientRequestAsrClose(reason = "client_stop") {
    return requestAsrClose(reason);
  };
  WSClient.recoverFromAsrFault = function wsClientRecoverFromAsrFault(reason) {
    return recoverFromAsrFault(reason);
  };
  WSClient.selfTestAudio = async function selfTestAudio() {
    console.log("[selfTestAudio] starting");
    try {
      const turnOpen = await openTurnOnce("self_test");
      if (!turnOpen) {
        console.warn("[selfTestAudio] FAIL: turn not open");
        return;
      }
      const started = await startRecorderStreaming(AppState?.policy || {}, "self_test");
      if (!started) {
        console.warn("[selfTestAudio] FAIL: recorder did not start");
        return;
      }
      console.log("[selfTestAudio] PASS: turn+recorder live");
    } catch (e) {
      console.warn("[selfTestAudio] FAIL:", e?.message || e);
    }
  };

  debug.encodeMessagePack = (value) => encodeMessagePack(value);
  debug.decodeMessagePack = (buffer) => decodeMessagePack(buffer);
  Object.defineProperty(WSClient, "socket", {
    configurable: true,
    enumerable: true,
    get() {
      return socket;
    }
  });
  WSClient.isConnected = function isConnected() {
    try {
      const s = typeof AppState?.getState === "function" ? AppState.getState() : AppState || {};
      const phase = s.wsPhase || s.connectionState || null;
      const phaseOk = typeof phase === "string" ? WS_READY_PHASES.has(phase) : true;
      return !!socket && socket.readyState === WebSocket.OPEN && phaseOk;
    } catch {
      return !!socket && socket.readyState === WebSocket.OPEN;
    }
  };
  WSClient.__debug = debug;
  window.WSClient = WSClient;
  WSClient._ws = WSClient._ws || null;
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("ws.close", () => {
      __resetAudioHeaderSent();
      resetTurnIntent("ws.close");
    });
    window.addEventListener("ws.resume_invalid", () => {
      __resetAudioHeaderSent();
      resetTurnIntent("ws.resume_invalid");
    });
  }
  if (typeof WSClient._linkedProofLogged !== "boolean") {
    WSClient._linkedProofLogged = false;
  }

  if (typeof WebSocket !== "undefined" && WebSocket?.prototype && !WebSocket.prototype.__wsClientGuarded) {
    const originalProtoSend = WebSocket.prototype.send;
    if (typeof originalProtoSend === "function") {
      WebSocket.prototype.send = function wsClientGuardedSend(data, ...rest) {
        if (this && this.__wsClientGuarding) {
          return originalProtoSend.apply(this, [data, ...rest]);
        }

        const isBinaryPayload = data instanceof Blob || data instanceof ArrayBuffer || ArrayBuffer.isView(data);
        if (!isBinaryPayload) {
          let typedPayload = null;

          if (typeof data === "string") {
            try {
              const parsed = JSON.parse(data);
              if (isTypedObjectPayload(parsed)) {
                typedPayload = parsed;
              }
            } catch (err) {
              console.warn("WebSocket.prototype.send: failed to parse string payload", err);
            }
          } else if (isTypedObjectPayload(data)) {
            typedPayload = data;
          }

          if (typedPayload) {
            if (!validateOutboundPayload(typedPayload, { rawPayload: data, source: "ws_prototype" })) {
              return undefined;
            }
            if (isTypedObjectPayload(data)) {
              try {
                data = JSON.stringify(data);
              } catch (err) {
                console.warn("WebSocket.prototype.send: failed to serialize payload", err);
                return undefined;
              }
            }
          }
        } else {
          const candidate = handleBinaryJsonPayload(data, { source: "ws_prototype_binary" });
          if (candidate === false) {
            return undefined;
          }
          if (candidate) {
            try {
              data = JSON.stringify(candidate);
            } catch (err) {
              console.warn("WebSocket.prototype.send: failed to stringify binary JSON payload", err);
              return undefined;
            }
          }
        }

        try {
          this.__wsClientGuarding = true;
          return originalProtoSend.apply(this, [data, ...rest]);
        } finally {
          try { delete this.__wsClientGuarding; } catch { this.__wsClientGuarding = undefined; }
        }
      };
      Object.defineProperty(WebSocket.prototype, "__wsClientGuarded", {
        value: true,
        configurable: true,
        enumerable: false,
        writable: false,
      });
    }
  }
})();
