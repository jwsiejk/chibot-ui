// CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram; no wake word.
/* __BUILD_MARKER__: FULL_DUPLEX_01 */
import { initVAD } from "./audio/vad_client.js";
import * as captureRuntimeModule from "./audio/capture_runtime.js";
import { initPcmSender } from "./audio/pcm_sender.js";
import { createWsAudioRuntime } from "./audio/ws_audio_runtime.js";
import { createPolicyRuntime } from "./ws/policy_runtime.js";
import { createWsConnection } from "./ws/connection.js";
import { createTurnRuntime } from "./ws/turns.js";
import { createBannerClient } from "./ws/banner_client.js";
import { createTranscriptBridge } from "./ws/transcript_bridge.js";
import { createSessionManager } from "./ws/session_manager.js";
import { createFrameParser } from "./ws/frame_parser.js";
import { encodeMessagePack, decodeMessagePack } from "./utils/msgpack.mjs";
import { isTypedArray, toArrayBuffer } from "./utils/binary.js";
import {
  MIC_OUTCOME,
  logMic,
  emitMicBreadcrumb,
  normalizeErrorDetail,
  recordLastError,
  recordClientBannerEvent,
  logStage,
} from "./ws/telemetry.js";

const captureRuntimeExports = captureRuntimeModule ?? {};
const { createCaptureRuntime } = captureRuntimeExports;

const AUDIO_KEEPALIVE_MS = 4000;

const USER_INITIATED_STOP_REASONS_FALLBACK = new Set([
  "user_requested",
  "user_restart",
  "user_end",
  "client_stop",
  "client_shutdown",
  "resume_invalid",
]);

const fallbackToReasonKey = (value) => {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim().toLowerCase();
  }
  if (typeof value === "object") {
    if (typeof value.reason === "string" && value.reason) {
      return value.reason.trim().toLowerCase();
    }
    if (typeof value.label === "string" && value.label) {
      return value.label.trim().toLowerCase();
    }
  }
  return String(value).trim().toLowerCase();
};

const fallbackReasonLooksUserInitiated = (value) => {
  const key = fallbackToReasonKey(value);
  if (!key) {
    return false;
  }
  return USER_INITIATED_STOP_REASONS_FALLBACK.has(key);
};

const reasonLooksUserInitiated = typeof captureRuntimeExports.reasonLooksUserInitiated === "function"
  ? captureRuntimeExports.reasonLooksUserInitiated
  : fallbackReasonLooksUserInitiated;

if (typeof createCaptureRuntime !== "function") {
  throw new Error("capture_runtime exports missing createCaptureRuntime()");
}
(() => {
  // ===== Shared constants, policy defaults, tiny helpers =====
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const JSON_SUBPROTOCOL = "chat.v2";
  const MSGPACK_SUBPROTOCOL = "chip-msgpack";
  const REQUESTED_CONTROL_CODEC = detectControlFramesCodec();
  const DEFAULT_SUBPROTOCOLS = REQUESTED_CONTROL_CODEC === "msgpack"
    ? [MSGPACK_SUBPROTOCOL, JSON_SUBPROTOCOL]
    : JSON_SUBPROTOCOL;
  const TOKEN_EXPIRY_MS = 60 * 1000;

  const IGNORED_VENDOR_MESSAGES = new Set(["AddPartialTranscript", "AddTranscript"]);
  const PCM_BREADCRUMB_POLICY = { input: 'pcm_16k', mode: 'pcm16' };
  const DEFAULT_ASR_VENDOR = 'gcp';
  const WS_READY_PHASES = new Set(['connected', 'ready', 'resuming']);
  let negotiatedControlCodec = REQUESTED_CONTROL_CODEC;

  function getNegotiatedControlCodec() {
    return negotiatedControlCodec === "msgpack" ? "msgpack" : "json";
  }

  function setNegotiatedControlCodec(codec) {
    negotiatedControlCodec = codec === "msgpack" ? "msgpack" : "json";
  }

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

  function sendTurnStop(reason = "vad_silence") {
    const ws = WSClient?._ws || window.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    const frame = {
      type: "input.stop",
      reason,
      ts: Date.now(),
    };

    try {
      ws.send(JSON.stringify(frame));
      console.log("client.turn_stop", frame);
    } catch (err) {
      console.warn("client.turn_stop_failed", { err, frame });
    }
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
  let turnStopSent = false;
  let speechSeenThisTurn = false;

  function resetTurnStopFlag() {
    // Start each user turn fresh: allow exactly one client-side input.stop
    // signal per turn and never block audio based on this flag.
    turnStopSent = false;
  }

  function resetSpeechFlag() {
    speechSeenThisTurn = false;
  }

  function maybeSendTurnStop(reason = "vad_silence") {
    if (turnStopSent) {
      return false;
    }
    const key = fallbackToReasonKey(reason) || "vad_silence";
    sendTurnStop(key);
    turnStopSent = true;
    return true;
  }

  async function handleVadSilenceStop(reason = "vad_silence") {
    const normalized = fallbackToReasonKey(reason) || "vad_silence";

    // Only end the turn if we’ve actually heard speech this turn.
    if (!speechSeenThisTurn) {
      // Pre-speech idle silence: do NOT stop the recorder or send input.stop.
      // Optionally: log or nudge UI, but don’t close the turn.
      try { AppState?.hub?.log?.('client.vad.idle_silence_ignored', { reason: normalized }); } catch {}
      return;
    }

    // Post-speech EOT silence: now we can end the turn.
    maybeSendTurnStop(normalized);
    try {
      // IMPORTANT: stopRecorder expects a string reason, not an object.
      await stopRecorder(normalized, {
        fallbackReason: "vad_silence",
        source: "client.vad_silence",
        allowVadStop: true,
      });
    } catch (err) {
      try { console.warn("vad_silence_stop_failed", err); } catch {}
    }
  }

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

  // Tracks banner state, cleans environment info, and surfaces WS connection toasts.
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

  let captureRuntime = null;

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

  // Handles client-side policy normalization and access helpers.
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

  let userGestureSatisfied = !AppState?.policy?.require_user_gesture_first_visit;

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

  // Owns PCM ring buffer, PCM sender wiring, and ASR priming from recent audio.
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
    getVadController: () => (captureRuntime ? captureRuntime.getVadController() : null),
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

  const consoleBus = resolveConsoleBusFunction();

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
    const bus = consoleBus || resolveConsoleBusFunction();
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

  // Bridges VAD events into AppState and orchestrates mic lifecycle + silence timers.
  captureRuntime = createCaptureRuntime({
    AppState,
    policyRuntime,
    audioRuntime,
    initVAD,
    consoleBus,
    hubLog,
    logStage,
    recordClientBannerEvent,
    schedulePartialWatchdog,
    clearPartialWatchdog,
    resetTurnIntent,
    MIC_OUTCOME,
    onVadSilenceStop: handleVadSilenceStop,
    canAutoStopFromVad: () => speechSeenThisTurn === true,
  });

  const {
    getVadController,
    setVadAppState,
    publishVad,
    handleVadGateChange,
    getClientVadPolicyConfig,
    getVadSilenceTimeoutMs,
    scheduleVadSilenceTimer,
    clearVadSilenceTimer,
    initClientVad,
    evaluateStopRecorderReason,
    setAppStateValue,
    setListeningState,
    setAsrArmInFlight,
    setWsConnected,
    setWsPhase,
    resetRecorderTelemetry,
    performStopRecorder,
    stopRecorder: captureStopRecorder,
    startRecorderStreaming,
  } = captureRuntime;

  const _origHandleVadGateChange = handleVadGateChange;
  function handleVadGateChangeWrapped(next) {
    try {
      if (next && (next.vadSpeech === true || next.speech === true)) {
        speechSeenThisTurn = true;
      }
    } catch {}
    return _origHandleVadGateChange(next);
  }

  async function stopRecorder(reason, options = {}) {
    const opts = (options && typeof options === "object" && !Array.isArray(options)) ? options : {};
    const normalized =
      fallbackToReasonKey(reason) ||
      fallbackToReasonKey(opts.fallbackReason) ||
      "unspecified";

    // Only send a client-side input.stop when explicitly allowed by caller
    // (e.g., VAD silence, manual stop). Do NOT use this to decide whether to
    // send audio; recorder lifecycle handles that.
    if (opts.allowVadStop === true) {
      maybeSendTurnStop(normalized);
    }

    // Always pass a normalized string reason into the capture runtime.
    return captureStopRecorder(normalized, opts);
  }

  WSClient.startRecorderStreaming = function wsClientStartRecorderStreaming(policy = {}, source = "manual") {
    if (!captureRuntime || typeof startRecorderStreaming !== "function") {
      console.warn("WSClient.startRecorderStreaming called but captureRuntime is not ready");
      return;
    }
    try {
      // Ensure a fresh turn-stop guard when we locally begin a new capture/turn.
      resetTurnStopFlag();
      resetSpeechFlag();
      return startRecorderStreaming(policy, source);
    } catch (err) {
      console.warn("WSClient.startRecorderStreaming failed", err);
    }
  };

  initClientVad(handleVadGateChangeWrapped);

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
  function clearPendingRearm() {
    awaitingTurnEndForRearm = false;
    pendingRearmReason = null;
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

  // Delivers ASR/chat frames into the transcript UI and tracks streaming state.
  const transcriptBridge = createTranscriptBridge({
    AppState,
    hubLog,
    logStage,
    dispatchFrame,
  });

  const {
    deliverAsr,
    deliverChat,
    handleChatHistoryFrame,
    transcriptFrameAllowed,
    attachTranscriptView,
    handleAssistantStreamingBegin,
    handleAssistantStreamingDelta,
    handleAssistantStreamingCommit,
    handleAssistantStreamingEnd,
  } = transcriptBridge;

  window.attachTranscriptView = function attachTranscriptViewGlobal(view) {
    attachTranscriptView(view);
  };

  function handleIncomingFrame(frame) {
    return handleMessageFrame(frame);
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
      case "begin":
        try { handleAssistantStreamingBegin(frame); } catch (e) { console.warn("begin err", e); }
        dispatchFrame(frame);
        return;

      case "chat.delta":
      case "delta":
        try { handleAssistantStreamingDelta(frame); } catch (e) { console.warn("delta err", e); }
        dispatchFrame(frame);
        return;

      case "chat.commit":
      case "commit":
        try { handleAssistantStreamingCommit(frame); } catch (e) { console.warn("commit err", e); }
        dispatchFrame(frame);
        return;

      case "chat.end":
      case "end":
        try { handleAssistantStreamingEnd(frame); } catch (e) { console.warn("end err", e); }
        dispatchFrame(frame);
        return;

      case "chat.message":
      case "message":
        try { deliverChat(frame); } catch (e) { console.warn("deliverChat error", e); }
        dispatchFrame(frame);
        return;

      case "chat.history":
      case "history":
        try { handleChatHistoryFrame(frame); } catch (e) { console.warn("history error", e); }
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
        await sessionClose("bad_info_sequence");
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
      resetTurnStopFlag();
      resetSpeechFlag();
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

  // Wraps the WebSocket transport, queue, and heartbeat, forwarding frames downstream.
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

  const frameParser = createFrameParser({
    hubLog,
    logStage,
    connection,
    handleMessageFrame: (frame) => {
      if (connection && typeof connection.handleParsedFrame === "function") {
        return connection.handleParsedFrame(frame);
      }
      return handleMessageFrame(frame);
    },
    handleErrorFrame,
    getNegotiatedControlCodec,
    getAudioPlayer,
    ignoredVendorMessages: IGNORED_VENDOR_MESSAGES,
  });

  const {
    normalizeIncomingFrame,
    processControlFrameObject,
    parseMessageData,
    handleRawMessageData,
  } = frameParser;

  if (connection && typeof connection.setRawMessageHandler === "function") {
    connection.setRawMessageHandler(handleRawMessageData);
  }

  const sessionManager = createSessionManager({
    AppState,
    connection,
    captureRuntime,
    bannerClient,
    logStage,
    recordClientBannerEvent,
    hubLog,
    recordLastError,
    DEFAULT_SUBPROTOCOLS,
    DEFAULT_CLOSE_REASON,
    TOKEN_EXPIRY_MS,
    getAudioStreaming: () => _audioStreaming,
    setAudioStreaming: (value) => { _audioStreaming = Boolean(value); },
    ensurePcmSender,
    resetAudioHeaderSent: () => __resetAudioHeaderSent(),
    isTypedObjectPayload,
    validateOutboundPayload,
  });

  const {
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
    open: sessionOpen,
    close: sessionClose,
  } = sessionManager;

  // Coordinates turn state and ASR lifecycle (open/arm/close/recover decisions).
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

    const transient = !detailReason
      || detailReason === "heartbeat_timeout"
      || closeCode === 1001
      || closeCode === 1006;

    if (transient) {
      setTimeout(() => {
        try {
          if (typeof attemptAutoResume === "function" && attemptAutoResume()) {
            return;
          }
          if (WSClient && typeof WSClient.open === "function") {
            WSClient.open();
          }
        } catch {}
      }, 300);
    }
  });

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
      await sessionClose("bad_info_frame");
      return;
    }
    expectInfoFrame = false;
    // clearInfoWatchdog(); // ❌ removed – connection.js already does this
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
      await sessionClose("resume_invalid");
    }
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

  function validateOutboundPayload(payload, { rawPayload = payload, source = "wsclient" } = {}) {
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

      try {
        console.warn("WSClient send skipped payload with non type-preserving structure", {
          structure,
          keys,
          source,
        });
      } catch {}

      try {
        recordClientBannerEvent("ws.send.invalid_payload", {
          reason: "non_type_preserving_structure",
          structure,
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

    const type =
      payload && typeof payload.type === "string"
        ? payload.type.trim()
        : "";

    if (type.length > 0) {
      return true;
    }

    // Missing `type` field – treat as invalid and log
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "missing_type_field",
        source,
      });
    } catch {}

    try {
      logStage("client.ws", {
        outcome: "send_skipped_missing_type_field",
        source,
      });
    } catch {}

    return false;
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
    },
    normalizeIncomingFrame,
    processControlFrameObject,
    parseMessageData,
    handleRawMessageData,
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

  WSClient.open = function wsClientOpen(options = {}, protocolsOverride) {
    const ws = sessionOpen(options, protocolsOverride);
    socket = ws || null;
    if (!ws) {
      try { WSClient._ws = null; } catch {}
    }
    return ws;
  };
  WSClient.close = function wsClientClose(reason) {
    const result = sessionClose(reason);
    const finalize = () => {
      socket = null;
      try { WSClient._ws = null; } catch {}
    };
    if (result && typeof result.then === "function") {
      return result.then((value) => {
        finalize();
        return value;
      });
    }
    finalize();
    return result;
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
