// CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram; no wake word.
/* __BUILD_MARKER__: FULL_DUPLEX_01 */
import * as versionModule from "./version.js";
const importV = typeof versionModule.importV === "function"
  ? versionModule.importV
  : async (path) => import(/* @vite-ignore */ path);
import { createPolicyRuntime } from "./ws/policy_runtime.js";
import { createWsConnection } from "./ws/connection.js";
import { createTurnRuntime } from "./ws/turns.js";
import { createBannerClient } from "./ws/banner_client.js";
import { createTranscriptBridge } from "./ws/transcript_bridge.js";
import { createSessionManager } from "./ws/session_manager.js";
import { createFrameParser } from "./ws/frame_parser.js";
import { encodeMessagePack, decodeMessagePack } from "./utils/msgpack.mjs";
import { isTypedArray, toArrayBuffer } from "./utils/binary.js";
import { createVoicePhaseController, PHASE } from "./voice/phase_controller.js";
import {
  MIC_OUTCOME,
  logMic,
  emitMicBreadcrumb,
  normalizeErrorDetail,
  recordLastError,
  recordClientBannerEvent,
  logStage,
} from "./ws/telemetry.js";
import { getMicAudioContext, getPlaybackAudioContext } from "./audio/audio_core.js";

// Before any WS logic, confirm audio modules are loaded
if (typeof window !== "undefined" && typeof window.createWsAudioRuntime !== "function") {
  console.error("Audio runtime not loaded — forcing reload");
  window.__ASKCHIP_AUDIO_LOAD_FAILURE = true;
  // Hard fail open: this prevents silent failure mode
  // but does not block WS initialization.
}

// Additional diagnostic
try {
  if (typeof window !== "undefined") {
    console.log("AudioModuleCheck", {
      createWsAudioRuntime: typeof window.createWsAudioRuntime,
      createCaptureRuntime: typeof window.createCaptureRuntime,
    });
  }
} catch (_) {}

// Global uncaught error diagnostics
if (typeof window !== "undefined") {
  window.addEventListener("error", (e) => {
    try { window.emitClientLog("js_error", { msg: e.message, stack: e.error?.stack || null }); } catch (_) {}
  });
  window.addEventListener("unhandledrejection", (e) => {
    try { window.emitClientLog("unhandled_promise", { reason: String(e.reason) }); } catch (_) {}
  });
}

function wsDiag(tag, detail = {}) {
  try {
    console.debug("[WS-DIAG]", tag, detail);
    if (typeof window !== "undefined" && window.emitClientLog) {
      window.emitClientLog("ws_diag", { tag, ...detail });
    }
  } catch (_) {}
}

function getAppState() {
  return typeof window !== "undefined" ? window.AppState : undefined;
}

const AppState = getAppState();

const [
  vadModule,
  captureRuntimeModule,
  pcmSenderModule,
  wsAudioRuntimeModule,
] = await Promise.all([
  importV("/static/js/audio/vad_client.js"),
  importV("/static/js/audio/capture_runtime.js"),
  importV("/static/js/audio/pcm_sender.js"),
  importV("/static/js/audio/ws_audio_runtime.js"),
]);

const { initVAD } = vadModule ?? {};
const captureRuntimeExports = captureRuntimeModule ?? {};
const { createCaptureRuntime } = captureRuntimeExports;
const { initPcmSender } = pcmSenderModule ?? {};
const { createWsAudioRuntime } = wsAudioRuntimeModule ?? {};

try {
  logStage("client.audio_modules_loaded", {
    hasInitVAD: typeof initVAD === "function",
    hasCreateCaptureRuntime: typeof createCaptureRuntime === "function",
    hasInitPcmSender: typeof initPcmSender === "function",
    hasCreateWsAudioRuntime: typeof createWsAudioRuntime === "function",
  });
} catch (_) {}

const CONVERSATION_START_DELAY_MS = 350;
const voicePhaseController = createVoicePhaseController({ log: logStage });
let updatePcmSenderState = null;
const markTurnAudioChunk = null;
let frameParser = null;

try {
  if (typeof window !== "undefined") {
    window.voicePhaseController = voicePhaseController;
    window.PHASE = window.PHASE || PHASE;
    window.VOICE_PHASE = window.VOICE_PHASE || PHASE;
  }
} catch (_) {}

function getPhase() {
  return voicePhaseController.getPhase();
}

function isGreetPhase() {
  try {
    const phase = voicePhaseController?.getPhase?.();
    return phase === PHASE.Greet;
  } catch (_) {
    return false;
  }
}

function syncAppStatePhase(options = {}) {
  const force = options && options.force === true;
  if (!AppState || typeof AppState !== "object") return;
  if (!force && AppState.phase === getPhase()) return;
  const prev = AppState.phase;
  const nextPhase = getPhase();
  wsDiag("ws_phase_set", { phase: nextPhase });
  AppState.phase = nextPhase;
  try {
    updatePcmSenderState?.("phase_change");
  } catch (_) {}
  try {
    logStage("client.phase.change", { prev, next: nextPhase });
  } catch (_) {}
  try {
    console.log("client.phase.change", { prev, next: nextPhase });
  } catch (_) {}
}
const missingAudioExports = [];
if (typeof createCaptureRuntime !== "function") {
  missingAudioExports.push("createCaptureRuntime");
}
if (typeof initVAD !== "function") {
  missingAudioExports.push("initVAD");
}
if (typeof initPcmSender !== "function") {
  missingAudioExports.push("initPcmSender");
}
if (typeof createWsAudioRuntime !== "function") {
  missingAudioExports.push("createWsAudioRuntime");
}
if (missingAudioExports.length > 0) {
  const detail = {
    missing: missingAudioExports,
    vadModuleLoaded: Boolean(vadModule),
    captureRuntimeModuleLoaded: Boolean(captureRuntimeModule),
    pcmSenderModuleLoaded: Boolean(pcmSenderModule),
    wsAudioRuntimeModuleLoaded: Boolean(wsAudioRuntimeModule),
  };
  try {
    logStage("client.audio.init_error", detail);
  } catch (_) {}
  console.error("Audio runtime modules missing exports", detail);
  throw new Error(
    `Audio runtime initialization failed; missing exports: ${missingAudioExports.join(", ")}`
  );
}

const AUDIO_KEEPALIVE_MS = 1000;
const AUDIO_KEEPALIVE_IDLE_MS = 30000;

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
const WS_READY_PHASES = new Set(['connected', 'ready']);
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
  let __ttsEndCount = 0;
  let __secondGreetingTraceActive = false;
  let __secondGreetingTraceCompleted = false;
  let __secondGreetingTraceStartMs = 0;
  let __secondGreetingStartChunkCount = 0;
  let conversationStartTimer = null;
  let conversationBlockedLogged = false;
  let conversationDelayedLogged = false;
  let conversationStartPlanned = false;
  let conversationStartCommitted = false;
  let firstPostGreetMicStarted = false;
  let conversationAsrReady = false;
  let micAndPcmReady = false;
  let lastConversationAttemptLog = 0;
  let hasOpenedAsrForConversation = false;
  let pendingCloseReason = null;

  let _audioStreaming = false;
  let __micBaseEnabled = false;
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
  let greetUtteranceId = null;

  function queueFrameUntilInfo(frame) {
    pendingInfoGateFrames.push(frame);
  }

  async function flushPendingInfoGateFrames() {
    if (!pendingInfoGateFrames.length) {
      return;
    }
    const queued = pendingInfoGateFrames.splice(0);
    for (const queuedFrame of queued) {
      // eslint-disable-next-line no-await-in-loop
      await handleMessageFrame(queuedFrame);
    }
  }

  function logPhaseTransition(from, to, reason) {
    if (from === to) return;
    try {
      logStage("phase.transition", { from, to, reason: reason || null });
    } catch {}
  }

  function promoteReadyPhase(reason = "info_frame") {
    const prev = typeof AppState?.wsPhase === "string" && AppState.wsPhase ? AppState.wsPhase : null;
    logPhaseTransition(prev, "ready", reason);
    const nextPhase = "ready";
    wsDiag("ws_phase_set", { phase: nextPhase });
    try { connection?.setWsPhase?.(nextPhase); } catch {}
    try { setWsPhase?.(nextPhase); } catch {}
    try { updateState({ wsPhase: "ready", connectionState: "connected" }); } catch {}
  }

  function shouldQueueDuringInfoGate(frame) {
    const type = typeof frame?.type === "string" ? frame.type : "";
    if (!expectInfoFrame) {
      return false;
    }
    if (type === "policy.update") return true;
    if (type === "chat.history" || type === "history") return true;
    if (type === "asr.ready") return true;
    if (type.startsWith("meta.")) return true;
    return false;
  }

  function maybePromoteReadyAfterMicAudio(reason = "mic_audio_post_greet") {
    const phase = getPhase();
    const ttsActive = Boolean(AppState?.ttsActive);
    if (phase !== PHASE.Greet || ttsActive) {
      return;
    }
    try { logStage("phase.greet.receivedInfo", { frame: { type: reason } }); } catch {}
    voicePhaseController.markGreetEnd();
    syncAppStatePhase({ force: true });
    expectInfoFrame = false;
    promoteReadyPhase(reason);
  }

  function resetTurnStopFlag() {
    // Start each user turn fresh: allow exactly one client-side input.stop
    // signal per turn and never block audio based on this flag.
    turnStopSent = false;
  }

  function resetSpeechFlag() {
    speechSeenThisTurn = false;
  }

  function frameSignalsGreetStart(frame) {
    if (!frame || typeof frame !== "object") return false;
    if (frame.type === "greet" || frame.type === "greet.start" || frame.type === "greet.begin") {
      return true;
    }
    if (frame.type === "tts.start" && frame?.meta?.is_greet === true) {
      return true;
    }
    return false;
  }

  function frameSignalsGreetEnd(frame) {
    if (!frame || typeof frame !== "object") return false;

    // Trust explicit server signals
    if (frame.type === "greet.end" || frame.type === "greet.complete") {
      return true;
    }

    if (frame.type === "tts.end") {
      // Accept tts.end if we are currently in the Greet phase
      if (getPhase() === PHASE.Greet) {
        try {
          logStage("client.greet_end_detected_tts", {
            phase: getPhase(),
            type: frame.type,
            utt_id: frame?.utt_id || null,
          });
        } catch (_) {}
        return true;
      }

      // Fallback to explicit metadata if provided
      if (frame?.meta?.is_greet === true) {
        return true;
      }
    }

    return false;
  }

  function markGreetStart(frame) {
    if (getPhase() === PHASE.Greet) {
      return;
    }
    wsDiag("greet_start", { utt_id: frame?.utt_id });
    hasOpenedAsrForConversation = false;
    firstPostGreetMicStarted = false;
    conversationStartPlanned = false;
    conversationStartCommitted = false;
    clearConversationStartTimer();
    try {
      const audioCtx = getPlaybackAudioContext();
      if (audioCtx) {
        const node = audioCtx.createBufferSource();
        node.buffer = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
        const silentGain = typeof audioCtx.createGain === "function" ? audioCtx.createGain() : null;
        const silentDestination =
          typeof audioCtx.createMediaStreamDestination === "function"
            ? audioCtx.createMediaStreamDestination()
            : null;
        if (silentGain) {
          silentGain.gain.value = 0;
          if (silentDestination) {
            silentGain.connect(silentDestination);
          }
          node.connect(silentGain);
        }
        node.start(0);
        logStage("client.audio_context.pre_warm_for_greet", {});
      }
      if (window.AC_PREPARED !== true) {
        window.AC_PREPARED = true;
        if (audioCtx?.state === "suspended" && typeof audioCtx.resume === "function") {
          audioCtx.resume().catch(() => {});
        }
        logStage("client.audio_context.prepared_before_greet", { state: audioCtx?.state });
      }
    } catch (_) {}
    if (typeof frame?.utt_id === "string" && frame.utt_id) {
      greetUtteranceId = frame.utt_id;
    }
    voicePhaseController.markGreetStart(frame?.utt_id);
    syncAppStatePhase({ force: true });
    try {
      logStage("client.phase.greet_start", { phase: getPhase() });
    } catch (_) {}
    try {
      logStage("client.greet_start", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        utt_id: greetUtteranceId,
      });
      console.log("client.greet_start", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        utt_id: greetUtteranceId,
      });
    } catch (_) {}
    try {
      setBaseEnabled?.(false, "greet_start");
      logStage("client.greet_start.set_base_enabled", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
      });
      console.log("client.greet_start.set_base_enabled", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
      });
      setSenderPauseReason("greet", true);
      applySenderPausedState();
      updatePcmSenderState("greet_start");
    } catch (_) {}
    try {
      setAppStateValue?.("barge_in_enabled", false);
      if (AppState && typeof AppState === "object") {
        AppState.barge_in_enabled = false;
      }
    } catch (_) {}
    try {
      if (typeof WSClient?.stopRecorderStreaming === "function") {
        WSClient.stopRecorderStreaming("greet_start");
      } else {
        autoStopRecorder("greet_start", { force: true, allowVadStop: true });
      }
      try {
        logStage("client.greet.mic_stop", { phase: getPhase() });
      } catch (_) {}
    } catch (_) {}
  }

  function markGreetEnd(frame) {
    if (getPhase() !== PHASE.Greet) {
      return;
    }
    voicePhaseController.markGreetEnd(frame?.utt_id);
    syncAppStatePhase({ force: true });
    try {
      logStage("client.phase.greet_end", { phase: getPhase() });
    } catch (_) {}
    Promise.resolve().then(async () => {
      try { await ensureMicHardware(); } catch (_) {}
      const graphReady = await ensureAudioGraph("greet_to_conversation_ready");
      if (graphReady) {
        markMicAndPcmReady("audio_graph_live");
      }
      try {
        const track = typeof getMicTrack === "function" ? getMicTrack() : null;
        if (track) {
          track.enabled = true;
          logStage("client.mic.hardware_unmute", { phase: getPhase() });
        }
      } catch (_) {}
    });
    try {
      conversationStartPlanned = true;
      scheduleConversationStartAfterGreet("mark_greet_end");
    } catch (_) {}
  }

  function clearConversationStartTimer() {
    if (conversationStartTimer) {
      clearTimeout(conversationStartTimer);
      conversationStartTimer = null;
    }
  }

  function isConversationReadyPhase() {
    const phase = getPhase();
    return phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
  }

  function markMicAndPcmReady(reason = "capture_runtime_ready") {
    if (micAndPcmReady) {
      return;
    }
    micAndPcmReady = true;
    try {
      logStage("client.mic_pcm.ready", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        reason,
      });
    } catch (_) {}
  }

  function markConversationAsrReady(reason = "asr_ready_frame") {
    if (conversationAsrReady) {
      return;
    }
    conversationAsrReady = true;
    try {
      logStage("client.conversation.asr_ready", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        reason,
      });
    } catch (_) {}
  }

  function isReadyForConversationStart() {
    const phase = voicePhaseController?.getPhase?.() || null;
    const phaseOk = phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
    const wsReady = AppState?.wsPhase === "ready";
    return phaseOk && wsReady && conversationAsrReady && micAndPcmReady;
  }

  function canBargeIn() {
    return isConversationReadyPhase();
  }

  function safeRequestAsrOpen(reason) {
    wsDiag("asr_open_request", { reason });
    try {
      logStage("client.asr_open.intent", {
        reason,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
      });
    } catch (_) {}
    const phase = voicePhaseController?.getPhase?.() || null;
    const allowed = phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
    if (!allowed) {
      try {
        logStage("client.asr_open.skipped", {
          reason,
          phase,
          wsPhase: AppState?.wsPhase || null,
          skipReason: "phase_not_allowed",
        });
      } catch (_) {}
      return;
    }
    try {
      if (typeof requestAsrArm === "function") {
        requestAsrArm(reason);
      }
    } catch (err) {
      console.warn("safeRequestAsrOpen: requestAsrArm failed", err);
    }

    try {
      logStage("client.asr_open.request", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        source: reason || null,
      });
      console.log("client.asr_open.request", {
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        source: reason || null,
      });
    } catch (_) {}

    try {
      logStage("client.asr_open.proceed", {
        reason,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
      });
    } catch (_) {}

    try {
      if (typeof openAsr === "function") {
        openAsr({ reason });
      }
    } catch (err) {
      console.warn("safeRequestAsrOpen: openAsr failed", err);
    }
  }

  async function safeStartRecorderStreaming(policy, source) {
    // Architectural rule: recorder may only start in ConversationReady/UserTurn.
    // No warm-ups in Greet/Boot; see ASKCHIP_CONVERSATIONAL_FLOW_REPORT.
    const phase = voicePhaseController?.getPhase?.() || null;
    const allowed = phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
    try {
      logStage("client.mic.start_intent", {
        source,
        phase,
        wsPhase: AppState?.wsPhase || null,
        allowed,
      });
    } catch (_) {}
    if (!allowed) {
      try {
        logStage("client.recorder.start_gated", {
          reason: "phase_block",
          phase,
          wsPhase: AppState?.wsPhase || null,
          source: source || null,
        });
        logStage("client.mic.start_blocked", {
          reason: "phase_not_conversation",
          source,
          phase,
          wsPhase: AppState?.wsPhase || null,
        });
      } catch (_) {}
      return false;
    }
    try {
      logStage("client.mic.start_proceed", {
        source,
        phase,
        wsPhase: AppState?.wsPhase || null,
      });
    } catch (_) {}
    let recorderStart = null;
    if (typeof WSClient?.startRecorderStreaming === "function") {
      recorderStart = WSClient.startRecorderStreaming(policy, source);
    } else if (typeof orchestratedStartCapture === "function") {
      recorderStart = orchestratedStartCapture(policy, source);
    }

    if (!recorderStart) {
      return false;
    }

    const started = await recorderStart;
    if (!started) {
      const canRetry =
        (source === "asr_ready_forced_start" || source === "server.start_listening") &&
        AppState?.asrReady &&
        !conversationStartCommitted &&
        typeof source === "string" &&
        !source.endsWith("_retry");

      if (canRetry) {
        try {
          logStage("client.mic.start_retry_scheduled", {
            source,
            phase,
            wsPhase: AppState?.wsPhase || null,
            delay_ms: 50,
          });
        } catch (_) {}
        try {
          await captureStopRecorder("retry_reset", { allowVadStop: true, source: `${source}_retry` });
        } catch (_) {}
        setTimeout(() => {
          safeStartRecorderStreaming(policy, `${source}_retry`);
        }, 50);
      }

      return false;
    }

    return started;
  }

  function enterConversationAfterGreet(source = "greet_tts_end") {
    if (!conversationStartPlanned || conversationStartCommitted) {
      clearConversationStartTimer();
      return;
    }

    const now = Date.now();
    if (now - lastConversationAttemptLog > 500) {
      wsDiag("conversation_attempt", { source });
      lastConversationAttemptLog = now;
    }

    try {
      logStage("client.enter_conversation_after_greet.intent", {
        source,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
        hasOpenedAsrForConversation,
      });
    } catch (_) {}

    const wsReady = WS_READY_PHASES.has(AppState?.wsPhase);
    const asrReady = Boolean(AppState?.asrReady);
    const phaseBefore = getPhase();
    const greetCompleted = phaseBefore === PHASE.ConversationReady || phaseBefore === PHASE.UserTurn;
    const livePcmStream = (() => {
      try {
        const track = getCaptureStream?.()?.getAudioTracks?.()[0] || null;
        if (!track) return false;
        return track.readyState === "live";
      } catch (_) {
        return false;
      }
    })();

    const shouldAutoStartMic =
      conversationStartPlanned &&
      !conversationStartCommitted &&
      !firstPostGreetMicStarted &&
      wsReady &&
      micAndPcmReady &&
      greetCompleted &&
      !asrReady &&
      !livePcmStream;

    if (shouldAutoStartMic) {
      firstPostGreetMicStarted = true;
      try {
        logStage("client.conversation.first_turn_mic_autostart", {
          source,
          wsPhase: AppState?.wsPhase || null,
          phase: phaseBefore,
        });
      } catch (_) {}
      try {
        const startResult = safeStartRecorderStreaming(AppState?.policy || {}, "post_greet_first_turn");
        if (startResult && typeof startResult.catch === "function") {
          startResult.catch((err) => {
            console.warn("first post-greet mic start failed", err);
          });
        }
      } catch (err) {
        console.warn("first post-greet mic start failed", err);
      }
    }

    const readyForUserTurn = wsReady && asrReady;

    if (readyForUserTurn && !conversationStartCommitted) {
      conversationStartCommitted = true;
      conversationDelayedLogged = false;
      clearConversationStartTimer();
      try {
        logStage("client.conversation.user_turn_commit", {
          source,
          wsPhase: AppState?.wsPhase || null,
          asrReady,
          livePcmStream,
        });
      } catch (_) {}
      if (phaseBefore === PHASE.Greet) {
        try {
          logStage("client.conversation.user_turn_commit.greet_end_fixup", {
            source,
            wsPhase: AppState?.wsPhase || null,
          });
        } catch (_) {}
        voicePhaseController.markGreetEnd();
      }
      voicePhaseController.enterConversation(source);
      syncAppStatePhase({ force: true });
      try {
        logStage("client.enter_conversation_after_greet.phase_set", {
          source,
          phase: voicePhaseController?.getPhase?.() || null,
          wsPhase: AppState?.wsPhase || null,
        });
      } catch (_) {}
      return;
    }

    if (!conversationStartCommitted && !isReadyForConversationStart()) {
      try {
        if (!wsReady && !conversationDelayedLogged) {
          logStage("client.conversation.delayed_until_ready", {
            phase: phaseBefore,
            wsPhase: AppState?.wsPhase || null,
            reason: "ws_not_ready",
          });
          conversationDelayedLogged = true;
        }
      } catch (_) {}

      if (!conversationStartTimer) {
        conversationStartTimer = setTimeout(
          () => {
            conversationStartTimer = null;
            enterConversationAfterGreet("wait_ready");
          },
          100
        );
      }
      return;
    }

    conversationDelayedLogged = false;
    conversationStartCommitted = true;
    clearConversationStartTimer();

    try {
      logStage("client.conversation.begin.committed", {
        source,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
      });
    } catch (_) {}

    if (phaseBefore === PHASE.Greet) {
      voicePhaseController.markGreetEnd();
    } else if (phaseBefore !== PHASE.ConversationReady && phaseBefore !== PHASE.UserTurn) {
      voicePhaseController.endUserTurn("conversation_ready_bridge");
    }
    syncAppStatePhase({ force: true });

    if (getPhase() === PHASE.ConversationReady) {
      try {
        logStage("client.conversation_ready", {
          phase: getPhase(),
          wsPhase: AppState?.wsPhase || null,
        });
      } catch (_) {}
    }

    if (getPhase() !== PHASE.UserTurn) {
      voicePhaseController.enterConversation(source);
      syncAppStatePhase({ force: true });
    }
    try {
      logStage("client.enter_conversation_after_greet.post_phase", {
        source,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
        hasOpenedAsrForConversation,
      });
    } catch (_) {}
    if (!hasOpenedAsrForConversation) {
      const skipAsrOpen = firstPostGreetMicStarted && asrReady;
      hasOpenedAsrForConversation = true;
      if (skipAsrOpen) {
        try {
          logStage("client.asr_open.skipped", {
            reason: "first_turn_already_opened_via_header",
            source,
            phase: getPhase(),
            wsPhase: AppState?.wsPhase || null,
          });
        } catch (_) {}
      } else {
        safeRequestAsrOpen("conversation_after_greet");
      }
    }

    const shouldStartMicNow =
      !firstPostGreetMicStarted || !conversationStartPlanned || conversationStartCommitted;

    if (shouldStartMicNow) {
      safeStartRecorderStreaming(AppState?.policy || {}, "conversation_after_greet");
    }
    try {
      logStage("client.enter_conversation_after_greet.asr_and_mic_called", {
        source,
        phase: voicePhaseController?.getPhase?.() || null,
        wsPhase: AppState?.wsPhase || null,
        hasOpenedAsrForConversation,
      });
    } catch (_) {}
    try {
      if (connection && typeof connection.setWsPhase === "function") {
        const nextPhase = "ready";
        wsDiag("ws_phase_set", { phase: nextPhase });
        connection.setWsPhase(nextPhase);
      }
      if (connection && typeof connection.flushQueuedFrames === "function") {
        wsDiag("ws_flush_queue");
        connection.flushQueuedFrames();
      }
    } catch (_) {}
    try {
      setSenderPauseReason("greet", false);
      applySenderPausedState();
      updatePcmSenderState("post_greet_phase_change");
    } catch (_) {}
    try {
      setBaseEnabled?.(true, "post_greet");
      logStage("client.conversation.begin.set_base_enabled", {
        source,
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        asrReady: Boolean(AppState?.asrReady),
      });
      console.log("client.conversation.begin.set_base_enabled", {
        source,
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        asrReady: Boolean(AppState?.asrReady),
      });
    } catch (_) {}
    try {
      setAppStateValue?.("barge_in_enabled", true);
      if (AppState && typeof AppState === "object") {
        AppState.barge_in_enabled = true;
      }
    } catch (_) {}
    try {
      logStage("client.conversation.begin", {
        source,
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        asrReady: Boolean(AppState?.asrReady),
      });
      console.log("client.conversation.begin", {
        source,
        phase: getPhase(),
        wsPhase: AppState?.wsPhase || null,
        asrReady: Boolean(AppState?.asrReady),
      });
    } catch (_) {}
  }

  function scheduleConversationStartAfterGreet(source = "greet_tts_end") {
    if (window.__gumFailed) {
      try {
        logStage("client.blocking_conversation_due_to_gum_failure", {});
      } catch (_) {}
      return;
    }
    if (!conversationStartPlanned) {
      conversationStartPlanned = true;
    }
    if (conversationStartCommitted) {
      return;
    }
    if (hasOpenedAsrForConversation) {
      return;
    }
    const phase = getPhase();
    const wsReady = AppState?.wsPhase === "ready";
    const greetCompleted = phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
    if (!wsReady || !greetCompleted) {
      try {
        if (!conversationBlockedLogged) {
          logStage("client.schedule_conversation_blocked_until_ready", {
            phase,
            wsPhase: AppState?.wsPhase || null,
            greetCompleted,
          });
          conversationBlockedLogged = true;
        }
      } catch (_) {}
      if (!conversationStartTimer) {
        conversationStartTimer = setTimeout(() => {
          conversationStartTimer = null;
          scheduleConversationStartAfterGreet("wait_ready");
        }, 50);
      }
      return;
    }
    clearConversationStartTimer();
    const delayMs = Math.max(0, Number(CONVERSATION_START_DELAY_MS) || 0);
    if (!conversationStartTimer) {
      conversationStartTimer = setTimeout(() => {
        conversationStartTimer = null;
        enterConversationAfterGreet(source);
      }, delayMs);
    }
    try {
      // we’re actually scheduling now – reset the spam guard
      conversationBlockedLogged = false;
      logStage("client.conversation.begin.scheduled", {
        source,
        delay_ms: delayMs,
        phase: getPhase(),
      });
    } catch (_) {}
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

    if (getPhase() === PHASE.Greet) {
      try { logStage("client.mic.auto_stop_suppressed", { phase: PHASE.Greet, reason: normalized }); } catch {}
      return;
    }

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
      setSenderPauseReason("turn_completed", true);
      applySenderPausedState();
      updatePcmSenderState();
      logStage("client.pcm.soft_pause", { reason: normalized });
    } catch (err) {
      try { console.warn("vad_silence_soft_pause_failed", err); } catch {}
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
        if (_audioStreaming) {
          stopRecorder("partial_watchdog_timeout", { force: true, allowVadStop: true, auto: true });
        } else {
          resetMicTurnState("partial_watchdog_timeout");
        }
        awaitingTurnEndForRearm = false;
        pendingRearmReason = null;
        speechSeenThisTurn = false;
        partialWatchdogFirstTurn = true;
        promoteReadyPhase("partial_watchdog_timeout");
      } catch {}
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
  let sendAudioKeepaliveNowImpl = () => false;

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

  function sendAudioKeepaliveNow() {
    try {
      return sendAudioKeepaliveNowImpl();
    } catch (err) {
      console.warn("sendAudioKeepaliveNow failed", err);
      return false;
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
  if (typeof AppState.on === "function" && !AppState.__keepaliveListeningHandlerInstalled) {
    AppState.__keepaliveListeningHandlerInstalled = true;
    AppState.on("listening", () => {
      scheduleAudioKeepalive();
    });
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
  const pendingInfoGateFrames = [];

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
      logStage("client.turn.intent", { action: "open", reason: reasonLabel });
    } catch {}
    return true;
  }

  function resetTurnIntent(reason) {
    if (__turnOpen) {
      __turnOpen = false;
      __turnOpenAt = 0;
      try {
        logStage("client.turn.intent", { action: "close", reason: reason || "reset" });
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
    __transportMisuseLogging = true;
    try {
      const logPayload = () => logStage("client.ws.misuse", { kind });
      // CRITICAL FIX: Decouple the hub log from the synchronous error handling flow
      if (typeof setTimeout === 'function') {
        setTimeout(logPayload, 0);
      } else {
        logPayload();
      }
    } catch (err) {
      try {
        console.warn("WS misuse log failed", err);
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

  syncAppStatePhase({ force: true });
  try {
    logStage("client.phase.init", { phase: getPhase() });
  } catch (_) {}
  function captureSecondGreetingMicSnapshot() {
    const listening = Boolean(AppState?.listening);
    const audioStreaming = Boolean(_audioStreaming);
    const micActive = Boolean((listening || audioStreaming) && !senderPaused);
    return {
      is_active: micActive,
      listening,
      audio_streaming: audioStreaming,
      sender_paused: Boolean(senderPaused),
      asr_ready: Boolean(AppState?.asrReady),
      asr_arm_in_flight: Boolean(AppState?.asrArmInFlight),
      warmup_until: warmupUntil || null,
    };
  }

  function captureSecondGreetingMediaSnapshot() {
    try {
      const snapshot = typeof getPcmSenderSnapshot === "function" ? getPcmSenderSnapshot() : {};
      if (snapshot && typeof snapshot === "object") {
        return snapshot;
      }
    } catch (err) {
      console.warn("captureSecondGreetingMediaSnapshot failed", err);
    }
    return {};
  }

  function logSecondGreetingTrace(event, extra = {}) {
    if (!__secondGreetingTraceActive && event !== "start") {
      return;
    }
    if (__secondGreetingTraceCompleted && event !== "first_chunk_send") {
      return;
    }
    const payload = {
      event,
      ts_ms: Date.now(),
      mic: captureSecondGreetingMicSnapshot(),
      media_stream: captureSecondGreetingMediaSnapshot(),
      chunk_count: Number.isFinite(AppState?.chunkCount) ? AppState.chunkCount : null,
      ...extra,
    };
    try {
      logStage("client.turn2.arm_trace", payload);
    } catch {}
  }

  function startSecondGreetingTrace(frame) {
    __secondGreetingTraceActive = true;
    __secondGreetingTraceCompleted = false;
    __secondGreetingTraceStartMs = Date.now();
    __secondGreetingStartChunkCount = Number.isFinite(AppState?.chunkCount) ? AppState.chunkCount : 0;
    logSecondGreetingTrace("start", { reason: "tts_end", utt_id: frame?.utt_id || null });
  }

  function handleFirstClientAudioFrameTelemetry(detail = {}) {
    if (!__secondGreetingTraceActive || __secondGreetingTraceCompleted) {
      return;
    }
    logSecondGreetingTrace("first_pcm_frame", detail || {});
  }

  function handleClientAudioChunkSendTelemetry(detail = {}) {
    try {
      if (typeof markTurnAudioChunk === "function") {
        markTurnAudioChunk(detail?.bytes);
      }
    } catch {}
    if (!__secondGreetingTraceActive || __secondGreetingTraceCompleted) {
      return;
    }
    __secondGreetingTraceCompleted = true;
    __secondGreetingTraceActive = false;
    const now = Date.now();
    const elapsedMs = __secondGreetingTraceStartMs ? Math.max(0, now - __secondGreetingTraceStartMs) : null;
    const currentChunks = Number.isFinite(AppState?.chunkCount) ? AppState.chunkCount : 0;
    const chunkDelta = Math.max(0, currentChunks - __secondGreetingStartChunkCount);
    logSecondGreetingTrace("first_chunk_send", { ...detail, elapsed_ms: elapsedMs, chunk_delta: chunkDelta });
  }

  function handleCaptureStopTelemetry(detail = {}) {
    if (!__secondGreetingTraceActive || __secondGreetingTraceCompleted) {
      return;
    }
    logSecondGreetingTrace("capture_stop", detail || {});
  }

  const sendAudioChunk = (payload, meta) => {
    if (WSClient && typeof WSClient.sendAudioChunk === "function") {
      return WSClient.sendAudioChunk(payload, meta);
    }
    return false;
  };

  const sendJSON = (payload) => {
    if (WSClient && typeof WSClient.sendJSON === "function") {
      WSClient.sendJSON(payload);
      return true;
    }
    return false;
  };

  const getCaptureStream = () => {
    if (captureRuntime && typeof captureRuntime.getCaptureStream === "function") {
      return captureRuntime.getCaptureStream();
    }
    return null;
  };

  // Owns PCM ring buffer, PCM sender wiring, and ASR priming from recent audio.
  const audioRuntime = createWsAudioRuntime({
    AppState,
    initPcmSender,
    updateState,
    logStage,
    onFirstClientAudioFrame: handleFirstClientAudioFrameTelemetry,
    onClientAudioChunkSend: handleClientAudioChunkSendTelemetry,
    getSocket: () => socket,
    WSClient,
    getWsClient: () => WSClient,
    sendAudioChunk,
    sendJSON,
    isAudioStreaming: () => _audioStreaming,
    canCaptureNow: () => canCaptureNow(),
    isSenderPaused: () => senderPaused,
    setSenderPauseReason,
    getCaptureStream: () => getCaptureStream?.(),
    getVadController: () => captureRuntime?.getVadController?.(),
    getFirstChunkSeen: () => __firstChunkSeen,
    setFirstChunkSeen: (value) => { __firstChunkSeen = Boolean(value); },
    getMicRecordingStartAt: () => __micRecordingStartAt,
    setMicRecordingStartAt: (value) => { __micRecordingStartAt = Number.isFinite(value) ? Number(value) : null; },
    getMicChunks: () => __micChunks,
    setMicChunks: (value) => { __micChunks = Number.isFinite(value) ? Number(value) : 0; },
    getMicBytes: () => __micBytes,
    setMicBytes: (value) => { __micBytes = Number.isFinite(value) ? Number(value) : 0; },
    getCurrentTurnReqId: () => getCurrentTurnReqId(),
  });

  if (typeof window !== "undefined") {
    try { window.audioRuntime = audioRuntime; } catch (_) {}
  }

  if (typeof window !== "undefined") {
    try {
      window.addEventListener("click", () => {
        [getMicAudioContext, getPlaybackAudioContext].forEach((fn) => {
          try {
            const ctx = fn();
            if (ctx?.state === "suspended") {
              ctx.resume().then(() => {
                logStage("client.audio_context.user_unlocked");
              }).catch(() => {});
            }
          } catch (_) {}
        });
      });
    } catch (_) {}
  }

  function updateMicBaseEnabled(enabled, reason) {
    if (!audioRuntime || typeof audioRuntime.setBaseEnabled !== "function") {
      return;
    }
    const normalized = Boolean(enabled);
    if (__micBaseEnabled === normalized) {
      return;
    }
    __micBaseEnabled = normalized;
    try {
      console.log(`[ws_client] baseEnabled set ${normalized ? "true" : "false"} from ${reason || "manual"}`);
    } catch (_) {}
    audioRuntime.setBaseEnabled(normalized, reason || "manual");
  }

  function applyAudioPolicy(policy) {
    const audio = policy && typeof policy === "object" && policy.audio && typeof policy.audio === "object"
      ? policy.audio
      : {};
    const keepaliveMs = (typeof audio.keepalive_ms === "number" && audio.keepalive_ms > 0)
      ? audio.keepalive_ms
      : AUDIO_KEEPALIVE_MS;
    const keepaliveIdleMs = (typeof audio.keepalive_idle_ms === "number" && audio.keepalive_idle_ms >= 0)
      ? audio.keepalive_idle_ms
      : AUDIO_KEEPALIVE_IDLE_MS;
    try {
      audioRuntime?.setAudioKeepaliveMs?.(keepaliveMs);
      audioRuntime?.setAudioKeepaliveIdleMs?.(keepaliveIdleMs);
    } catch (err) {
      console.warn("applyAudioPolicy failed", err);
    }
  }

  const {
    ensurePcmSender,
    handlePcmFrame,
    handlePcmSend,
    handleSampleRate,
    primeAsrStreamFromRing,
    recordRecorderChunk,
    getPcmRing,
    getPcmSenderSnapshot,
    setBaseEnabled,
    resetSilenceSuppression,
    updatePcmSenderState: runtimeUpdatePcmSenderState,
    scheduleAudioKeepalive: runtimeScheduleAudioKeepalive,
    clearAudioKeepaliveTimer: runtimeClearAudioKeepalive,
    sendAudioKeepaliveNow: runtimeSendAudioKeepaliveNow,
  } = audioRuntime;

  updatePcmSenderState = typeof runtimeUpdatePcmSenderState === "function"
    ? runtimeUpdatePcmSenderState
    : null;

  try {
    logStage("client.audio_runtime.wired", {
      hasSetBaseEnabled: typeof setBaseEnabled === "function",
      hasUpdatePcmSenderState: typeof updatePcmSenderState === "function",
      hasEnsurePcmSender: typeof ensurePcmSender === "function",
    });
    console.log("client.audio_runtime.wired", {
      hasSetBaseEnabled: typeof setBaseEnabled === "function",
      hasUpdatePcmSenderState: typeof updatePcmSenderState === "function",
      hasEnsurePcmSender: typeof ensurePcmSender === "function",
    });
  } catch (_) {}

  scheduleAudioKeepaliveImpl = typeof runtimeScheduleAudioKeepalive === "function"
    ? runtimeScheduleAudioKeepalive
    : () => {};
  clearAudioKeepaliveTimerImpl = typeof runtimeClearAudioKeepalive === "function"
    ? runtimeClearAudioKeepalive
    : () => {};
  sendAudioKeepaliveNowImpl = typeof runtimeSendAudioKeepaliveNow === "function"
    ? runtimeSendAudioKeepaliveNow
    : () => false;

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
    hubLog: logStage,
    logStage,
    recordClientBannerEvent,
    schedulePartialWatchdog,
    clearPartialWatchdog,
    resetTurnIntent,
    MIC_OUTCOME,
    onVadSilenceStop: handleVadSilenceStop,
    canAutoStopFromVad: () => speechSeenThisTurn === true,
    onCaptureStop: handleCaptureStopTelemetry,
  });
  try {
    enterConversationAfterGreet("capture_runtime_ready");
  } catch (_) {}

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
    ensureAudioGraph,
    ensureMicHardware,
    getMicTrack,
  } = captureRuntime;

  let currentTurnId = null;
  let micTurnState = "idle"; // "idle" | "starting" | "active" | "failed"
  let micTurnSource = null;
  const micStartSkipLogSet = new Set();
  let fallbackTurnCounter = 0;

  function logMicStartSkippedOnce(source, reason, turnId) {
    const key = `${source || "unknown"}|${turnId || "null"}|${reason || "unknown"}`;
    if (micStartSkipLogSet.has(key)) {
      return;
    }
    micStartSkipLogSet.add(key);
    try {
      logStage?.("client.mic_start_skipped", { source, reason, turnId });
    } catch (_) {}
  }

  function getActiveTurnId(policy = AppState?.policy || {}) {
    let turnId = null;
    try {
      if (typeof getCurrentTurnReqId === "function") {
        const candidate = getCurrentTurnReqId();
        if (typeof candidate === "string" && candidate) {
          turnId = candidate;
        }
      }
    } catch (_) {}

    if (!turnId) {
      try {
        if (frameParser && typeof frameParser.getCurrentTurnReqId === "function") {
          const candidate = frameParser.getCurrentTurnReqId();
          if (typeof candidate === "string" && candidate) {
            turnId = candidate;
          }
        }
      } catch (_) {}
    }

    if (!turnId) {
      try {
        if (typeof ensureTurnAudioReqId === "function") {
          turnId = ensureTurnAudioReqId(policy);
        }
      } catch (_) {}
    }

    if (!turnId) {
      fallbackTurnCounter += 1;
      turnId = `fallback-${fallbackTurnCounter}`;
    }

    return turnId;
  }

  function resetMicTurnState(reason = "turn_end") {
    if (micTurnState !== "idle") {
      try {
        logStage?.("client.mic_turn_reset", {
          reason,
          prevState: micTurnState,
          turnId: currentTurnId,
          source: micTurnSource,
        });
      } catch (_) {}
    }
    micTurnState = "idle";
    micTurnSource = null;
    currentTurnId = null;
    micStartSkipLogSet.clear();
  }

  async function orchestratedStartCapture(policy = AppState?.policy || {}, source = "manual") {
    const turnId = getActiveTurnId(policy);

    if (micTurnState === "active" && currentTurnId === turnId && turnId !== null) {
      logMicStartSkippedOnce(source, "already_active_for_turn", turnId);
      return true;
    }

    if (micTurnState === "starting" && currentTurnId === turnId && turnId !== null) {
      logMicStartSkippedOnce(source, "already_starting_for_turn", turnId);
      return true;
    }

    if (turnId !== currentTurnId && turnId !== null) {
      currentTurnId = turnId;
      micTurnState = "idle";
      micTurnSource = null;
      micStartSkipLogSet.clear();
    }

    micTurnState = "starting";
    micTurnSource = source;

    try {
      logStage?.("client.mic_start_attempt_orchestrated", {
        source,
        turnId,
        phase: getPhase(),
      });
    } catch (_) {}

    let ok = false;
    try {
      ok = await startRecorderStreaming({ policy, reason: source });
    } catch (err) {
      try { console.warn("orchestratedStartCapture failed", err); } catch (_) {}
      ok = false;
    }

    if (ok) {
      micTurnState = "active";
      try {
        logStage?.("client.mic_start_success_orchestrated", {
          source,
          turnId,
        });
      } catch (_) {}
    } else {
      micTurnState = "failed";
      try {
        logStage?.("client.mic_start_failed_orchestrated", {
          source,
          turnId,
        });
      } catch (_) {}
    }

    return ok;
  }

  function resetClientTtsGate(reason = "client_stop") {
    try {
      if (frameParser && typeof frameParser.resetTtsGate === "function") {
        frameParser.resetTtsGate(reason, { clearDescriptor: true });
      }
    } catch (_) {}
  }

  function handleVadGateChangeWrapped(next) {
    try {
      if (next && (next.vadSpeech === true || next.speech === true)) {
        if (!canBargeIn()) {
          return;
        }
        speechSeenThisTurn = true;
        setSenderPauseReason("turn_completed", false);
        applySenderPausedState();
        updatePcmSenderState();
      }
    } catch {}
    // The capture runtime will call handleVadGateChange after this override,
    // so we avoid invoking it here to prevent double processing.
  }

  async function stopRecorder(reason, options = {}) {
    const opts = (options && typeof options === "object" && !Array.isArray(options)) ? options : {};
    const normalized =
      fallbackToReasonKey(reason) ||
      fallbackToReasonKey(opts.fallbackReason) ||
      "unspecified";
    const autoStop = opts.auto === true || opts.autoStop === true || opts.isAutoStop === true;

    // Only send a client-side input.stop when explicitly allowed by caller
    // (e.g., VAD silence, manual stop). Do NOT use this to decide whether to
    // send audio; recorder lifecycle handles that.
    if (opts.allowVadStop === true) {
      maybeSendTurnStop(normalized);
    }

    try {
      logStage("client.mic", {
        outcome: MIC_OUTCOME.STOPPED,
        reason: normalized || "unknown",
        phase: getPhase(),
        auto: autoStop,
      });
    } catch (_) {}

    resetClientTtsGate("client_stop");

    // Always pass a normalized string reason into the capture runtime.
    const result = captureStopRecorder(normalized, opts);
    updateMicBaseEnabled(false, "mic_stop");
    try { resetMicTurnState("capture_stopped"); } catch (_) {}
    return result;
  }

  WSClient.stopRecorderStreaming = function wsClientStopRecorderStreaming(reason = "manual_stop") {
    try {
      resetClientTtsGate("client_stop");
      return stopRecorder(reason, { force: true });
    } catch (err) {
      console.warn("WSClient.stopRecorderStreaming failed", err);
      return false;
    }
  };

  function autoStopRecorder(reason, options = {}) {
    const opts = options && typeof options === "object" ? { ...options, auto: true } : { auto: true };
    const phase = voicePhaseController.getPhase();
    const normalizedReason = fallbackToReasonKey(reason) || "unspecified";
    if (phase === PHASE.Greet && opts.force !== true) {
      try {
        logStage("client.mic.auto_stop_suppressed", { phase, reason: normalizedReason });
      } catch (_) {}
      return false;
    }
    return stopRecorder(reason, opts);
  }

  WSClient.startRecorderStreaming = async function wsClientStartRecorderStreaming(policy = {}, source = "manual") {
    wsDiag("start_recorder_streaming", { source });
    const phase = getPhase();
    const allowed = phase === PHASE.ConversationReady || phase === PHASE.UserTurn;
    if (!allowed) {
      try {
        logStage?.("client.recorder.start_gated", {
          reason: "phase_block",
          phase,
          wsPhase: AppState?.wsPhase || null,
          source: source || null,
        });
        logStage?.("client.mic.start_blocked", {
          reason: "phase_not_conversation",
          source: source || null,
          phase,
          wsPhase: AppState?.wsPhase || null,
        });
      } catch (_) {}
      return false;
    }
    // Expose stopRecorder and input.stop helpers publicly so UI can pause mic on text input
    WSClient.stopRecorder = function wsClientStopRecorder(reason = "text_input", options = {}) {
      try {
        const opts = options && typeof options === "object" ? { ...options, force: true } : { force: true };
        return autoStopRecorder(reason, opts);
      } catch (err) {
        console.warn("WSClient.stopRecorder failed", err);
      }
    };
    WSClient.inputStop = function wsClientInputStop(reason = "text_input") {
      try {
        WSClient.sendJSON({ type: "input.stop", reason });
      } catch (err) {
        console.warn("WSClient.inputStop failed", err);
      }
    };
    WSClient.clearResume = function wsClientClearResume() {
      try { clearResumeState(); } catch (err) { console.warn('WSClient.clearResume failed', err); }
    };
    try {
      logStage("client.mic.start_requested", {
        source: source || "unknown",
        policy_audio: policy?.audio || null,
        phase: getPhase(),
      });
    } catch (_) {}
    if (!captureRuntime || typeof startRecorderStreaming !== "function") {
      console.warn("WSClient.startRecorderStreaming called but captureRuntime is not ready");
      try {
        logStage("client.mic.start_failed", { source: source || "unknown", reason: "no_capture_runtime", phase: getPhase() });
      } catch (_) {}
      return false;
    }
    try {
      // Ensure a fresh turn-stop guard when we locally begin a new capture/turn.
      resetTurnStopFlag();
      resetSpeechFlag();
      const started = await orchestratedStartCapture(policy, source);
      if (!started) {
        try {
          logStage("client.recorder.start_failed", {
            source: source || "unknown",
            reason: "startRecorderStreaming_returned_false",
            phase: getPhase(),
            wsPhase: AppState?.wsPhase || null,
          });
        } catch (_) {}
        try {
          logStage("client.mic.start_skipped", {
            source: source || "unknown",
            reason: "startRecorderStreaming_returned_false",
            phase: getPhase(),
          });
        } catch (_) {}
        return false;
      }
      _audioStreaming = true;
      markMicAndPcmReady("mic_start_success");
      setSenderPauseReason("server", false);
      setSenderPauseReason("tts", false);
      updatePcmSenderState();
      updateMicBaseEnabled(true, "mic_start");
      return started;
    } catch (err) {
      console.warn("WSClient.startRecorderStreaming failed", err);
      try {
        logStage("client.mic.start_failed", {
          source: source || "unknown",
          message: err?.message || "mic_start_failed",
          phase: getPhase(),
        });
      } catch (_) {}
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

  // Shared turn-audio request id for header + PCM chunks
  let currentTurnAudioReqId = null;

  function getCurrentTurnReqId() {
    return currentTurnAudioReqId;
  }

  function ensureTurnAudioReqId(policy = AppState?.policy || {}) {
    // If we already have an id for this turn, reuse it
    if (currentTurnAudioReqId) {
      return currentTurnAudioReqId;
    }

    // Optional: lane label for telemetry
    let lane = "mic";
    try {
      if (policy && typeof policy === "object") {
        if (typeof policy.input_lane === "string" && policy.input_lane) {
          lane = policy.input_lane;
        } else if (typeof policy.capture?.lane === "string" && policy.capture.lane) {
          lane = policy.capture.lane;
        }
      }
    } catch (_) {}

    const nowPart = Date.now().toString(36);
    const randPart = Math.random().toString(36).slice(2, 8);

    currentTurnAudioReqId = `req-${lane}-${nowPart}${randPart}`;

    try {
      logStage("client.turn_req_id.set", {
        reqId: currentTurnAudioReqId,
        lane,
      });
    } catch (_) {}

    return currentTurnAudioReqId;
  }

  function resetTurnAudioContext() {
    try {
      logStage("client.turn_audio_context.reset", {
        prevReqId: currentTurnAudioReqId || null,
      });
    } catch (_) {}
    currentTurnAudioReqId = null;

    // keep your existing audio-header state resets here (e.g. audioHeaderSent = false)
    __resetAudioHeaderSent();
    // ... any other existing reset logic ...
    resetMicTurnState("turn_audio_context_reset");
  }

  // --- Begin: header idempotency + strict schema ---
  let __audioHeaderSent = false;
  function __resetAudioHeaderSent() {
    __audioHeaderSent = false;
  }
  function __buildStrictAudioHeader(frameOrPolicy, headerOptions = {}) {
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

    const requestedRate = Number(headerOptions?.sampleRateHz);
    const sampleRate = Number.isFinite(requestedRate) && requestedRate > 0
      ? requestedRate
      : (Number.isFinite(normalizedRate) ? normalizedRate : base.sample_rate);
    const channels = Number.isFinite(normalizedChannels) && normalizedChannels > 0
      ? normalizedChannels
      : base.channels;

    const reqId = typeof headerOptions?.reqId === "string" && headerOptions.reqId
      ? headerOptions.reqId
      : null;
    if (!reqId) {
      throw new Error("audio.header requires req_id");
    }

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
      req_id: reqId,
      format: "pcm16",
      sample_rate: sampleRate,
      channels,
      meta: {
        encoding: "LINEAR16",
        sample_rate_hz: sampleRate,
        channels,
      },
      codec: "pcm_s16le",
    };
  }
  function __sendAudioHeaderOnce(frameOrMeta = {}) {
    if (__audioHeaderSent) {
      try { console.warn("audio.header already sent; skipping"); } catch {}
      return true;
    }

    const sendJSON = (WSClient && typeof WSClient.sendJSON === "function")
      ? WSClient.sendJSON.bind(WSClient)
      : null;
    if (!sendJSON) {
      console.warn("Failed to send audio header: WSClient.sendJSON unavailable");
      return false;
    }

    let header;
    try {
      const reqId = typeof frameOrMeta?.reqId === "string" && frameOrMeta.reqId
        ? frameOrMeta.reqId
        : (typeof frameOrMeta?.req_id === "string" && frameOrMeta.req_id ? frameOrMeta.req_id : null);
      header = __buildStrictAudioHeader(frameOrMeta, { ...frameOrMeta, reqId });
    } catch (err) {
      console.warn("Failed to build audio header", err);
      return false;
    }

    const markSent = () => {
      try { logStage("client.audio_header_send", header); } catch {}
      __audioHeaderSent = true;
      return true;
    };

    try {
      try {
        logStage("client.audio_header.send_intent", {
          type: header?.type || "unknown",
          format: header?.format || null,
          sample_rate: header?.sample_rate || null,
          channels: header?.channels || null,
        });
      } catch (_) {}
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
  function sendAudioHeader(frameOrMeta) {
    const policy = AppState?.policy || {};
    let reqId = typeof getCurrentTurnReqId === "function"
      ? getCurrentTurnReqId()
      : null;

    // Last-chance: allocate if missing
    if (!reqId && typeof ensureTurnAudioReqId === "function") {
      reqId = ensureTurnAudioReqId(policy);
    }

    if (!reqId) {
      console.warn("sendAudioHeader skipped: missing req_id");
      try {
        logStage("client.audio_header.skipped", {
          reason: "missing_reqId",
          wsPhase: AppState?.wsPhase || null,
        });
      } catch (_) {}
      return false;
    }

    // Merge frame/meta with reqId
    const meta = {};
    if (frameOrMeta && typeof frameOrMeta === "object") {
      Object.assign(meta, frameOrMeta);
    }
    meta.reqId = reqId;

    try {
      // This should be the ONLY place __sendAudioHeaderOnce is called
      return __sendAudioHeaderOnce(meta);
    } catch (err) {
      console.warn("sendAudioHeader failed", err);
      return false;
    }
  }
  // --- End: header idempotency + strict schema ---
  let __lastErrorSig = null, __lastErrorAt = 0;
  function clearPendingRearm() {
    awaitingTurnEndForRearm = false;
    pendingRearmReason = null;
  }

  function openAsr(opts = {}) {
    if (!isConversationReadyPhase()) {
      try {
        logStage("client.asr_open.skipped", {
          reason: "not_conversation_phase",
          phase: getPhase(),
          requested_reason: opts?.reason || null,
        });
      } catch (_) {}
      return undefined;
    }
    try {
      logStage("client.asr", {
        stage: "open_request",
        vendor: AppState.asrVendor || null,
      });
    } catch (_) {}
    if (typeof runtimeOpenAsr === "function") {
      return runtimeOpenAsr(opts);
    }
    return undefined;
  }

  function requestAsrArm(reason) {
    if (!isConversationReadyPhase()) {
      try {
        logStage("client.asr_open.skipped", {
          reason: "not_conversation_phase",
          phase: getPhase(),
          requested_reason: reason || null,
        });
      } catch (_) {}
      return undefined;
    }
    if (__secondGreetingTraceActive && !__secondGreetingTraceCompleted) {
      logSecondGreetingTrace("request_asr_arm", {
        reason: typeof reason === "string" ? reason : null,
        asr_ready: Boolean(AppState?.asrReady),
        sender_paused: Boolean(senderPaused),
      });
    }
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
    } else if (typeof globalThis?.startRecording === "function") {
      handler = globalThis.startRecording;
    }
    try {
      [getMicAudioContext, getPlaybackAudioContext].forEach((fn) => {
        try {
          const ctx = fn();
          if (ctx?.state === "suspended" && typeof ctx.resume === "function") {
            ctx.resume()
              .then(() => {
                logStage("client.audio_context.resume_on_gesture", {});
              })
              .catch((err) => {
                logStage("client.audio_context.resume_on_gesture_failed", { err: String(err) });
              });
          }
        } catch (_) {}
      });
    } catch (_) {}
    if (handler) {
      const context = typeof window !== "undefined" ? window : null;
      return handler.call(context, { trigger });
    }
    if (typeof startRecorderStreaming === "function") {
      return safeStartRecorderStreaming(AppState?.policy || {}, trigger || "invoke");
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
    hubLog: logStage,
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

    if (frame.type === "turn.empty") {
      try {
        logStage("client.turn.empty", {
          sid: frame.sid || null,
          turnIndex: frame.turn_index ?? null,
          reason: frame.reason || null,
        });
      } catch (_) {}

      try {
        setListeningState?.(false);
      } catch (_) {}

      try {
        resetMicTurnState("turn_frame_complete");
      } catch (_) {}

      try {
        if (typeof AppState.emit === "function") {
          AppState.emit("turnEmpty", {
            sid: frame.sid || null,
            turnIndex: frame.turn_index ?? null,
            reason: frame.reason || null,
          });
        }
      } catch (_) {}

      return;
    }

    if (frameSignalsGreetStart(frame)) {
      markGreetStart(frame);
    }
    if (frameSignalsGreetEnd(frame)) {
      markGreetEnd(frame);
    }

    if (frame.type === "server.conversation_ready") {
      conversationStartPlanned = true;
      try { clearConversationStartTimer(); } catch (_) {}
      try { enterConversationAfterGreet("server.conversation_ready"); } catch (_) {}
      return;
    }

    if (expectInfoFrame) {
      // Allow certain frames to bypass the info gate and be handled
      // by the existing handlers further down, just like before.
      if (frame.type === "server.banner" || frame.type === "policy.interaction") {
        // fall through; do NOT treat as bad_info_sequence
      } else {
        try { logStage("phase.greet.expectInfo", { frame }); } catch {}
        if (frame.type === "info") {
          expectInfoFrame = false;
          try { logStage("phase.greet.receivedInfo", { frame }); } catch {}
          await handleInfoFrame(frame);
          promoteReadyPhase("info_frame");
          await flushPendingInfoGateFrames();
          if (typeof WSClient?.emit === "function") {
            try {
              WSClient.emit("frame", frame);
            } catch (err) {
              console.warn("WSClient frame emit failed", err);
            }
          }
          return;
        }
        if (frame.type === "error") {
          await handleErrorFrame(frame);
          if (typeof WSClient?.emit === "function") {
            try {
              WSClient.emit("frame", frame);
            } catch (err) {
              console.warn("WSClient frame emit failed", err);
            }
          }
          return;
        }
        if (shouldQueueDuringInfoGate(frame)) {
          queueFrameUntilInfo(frame);
          return;
        }
        console.error("Expected info frame first, received", frame.type);
        await requestSessionShutdown("bad_info_sequence");
        return;
      }
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
        try {
          const finalText = frame?.text ?? frame?.transcript ?? frame?.final ?? null;
          logStage("client.asr", {
            stage: "final",
            text: typeof finalText === "string" ? finalText : null,
            isPartial: false,
          });
        } catch (_) {}
        if (transcriptFrameAllowed(frame)) {
          deliverAsr(frame);
        } else {
          logStage("ui_transcript_filter", { allow: false, type: frame.type });
        }
        handledByTranscriptDispatch = true;
        break;

      case "asr.open":
        try {
          logStage("client.asr", {
            stage: "open",
            vendor: AppState.asrVendor || null,
          });
        } catch (_) {}
        break;

      case "asr.error":
        if (frame?.code === "already_open") {
          try {
            logStage("client.asr.error.already_open", {
              phase: getPhase(),
              code: frame?.code || null,
              detail: frame?.detail || null,
            });
          } catch (_) {}
          return;
        }
        break;

      case "asr.close":
        try {
          const closeReason = typeof frame?.reason === "string" && frame.reason ? frame.reason : null;
          logStage("client.asr", {
            stage: "close",
            vendor: AppState.asrVendor || null,
            reason: closeReason,
          });
        } catch (_) {}
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
      if (frame.type === "asr.ready") {
        try {
          scheduleConversationStartAfterGreet("asr.ready_frame");
        } catch (_) {}
      }
      await handleAsrStateFrame(frame);
      if (frame.type === "turn.end") {
        try {
          voicePhaseController.endUserTurn("turn_end");
          syncAppStatePhase();
        } catch (_) {}
      }
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
        logStage("client.audio.throttle", { ms, until: __pauseSendUntil });
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
          try { logStage('client.audio.resume_after_throttle', { at: Date.now() }); } catch {}
        }, delay);
      } else {
        try { logStage('client.audio.resume_after_throttle', { at: Date.now() }); } catch {}
        if (AppState && typeof AppState === "object") {
          AppState._throttleUntil = 0;
        }
      }
      return;
    }

    if (frame.type === "config.updated" || frame.type === "config_updated") {
      const sourcePolicy = frame && typeof frame === 'object' ? frame.policy : null;
      const appliedPolicy = applyPolicySnapshotFromSource(sourcePolicy, 'config.updated');
      applyAudioPolicy(appliedPolicy);
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
      applyAudioPolicy(appliedPolicy);
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

    if (frame.type === "info") {
      await handleInfoFrame(frame);
    } else if (frame.type === "server.pong") {
      handlePongFrame(frame);
    } else if (frame.type === "pong") {
      handlePongFrame(frame);
    } else if (frame.type === "error") {
      await handleErrorFrame(frame);
    } else if (frame.type === "tts.start") {
      const shouldMuteDuringTts = Boolean(AppState?.policy?.recorder?.mute_send_during_tts);
      if (shouldMuteDuringTts) {
        try {
          setSenderPauseReason("tts", true);
          applySenderPausedState();
          updatePcmSenderState();
        } catch (err) {
          try { console.warn("soft_pause_on_tts_start_failed", err); } catch {}
        }
      }
      setAppStateValue("ttsActive", true);
      AppState.tts = true;
      window.requestAnimationFrame(() => window.AppUI?.refresh?.());
      if (typeof AppState.emit === "function") {
        AppState.emit("ttsActive", { active: true });
      }
      if (frame?.meta?.is_greet === true) {
        try {
          const audioCtx = getPlaybackAudioContext();
          if (audioCtx) {
            const node = audioCtx.createBufferSource();
            node.buffer = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
            const silentGain = typeof audioCtx.createGain === "function" ? audioCtx.createGain() : null;
            const silentDestination =
              typeof audioCtx.createMediaStreamDestination === "function"
                ? audioCtx.createMediaStreamDestination()
                : null;
            if (silentGain) {
              silentGain.gain.value = 0;
              if (silentDestination) {
                silentGain.connect(silentDestination);
              }
              node.connect(silentGain);
            }
            node.start(0);
            logStage("client.audio_context.warmup_output_before_greet", {});
          }
        } catch (_) {}
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
      try {
        setSenderPauseReason("tts", false);
        applySenderPausedState();
        updatePcmSenderState();
      } catch (err) {
        try { console.warn("clear_soft_pause_on_tts_end_failed", err); } catch {}
      }

      // Avoid hard-stopping the mic here so the ASR keep-alive stream remains intact.
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

      __ttsEndCount += 1;
      if (__ttsEndCount === 2) {
        startSecondGreetingTrace(frame);
      }

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
        console.warn(
          "Received start_listening before ASR ready; ignoring until asr.ready arrives.",
          frame
        );
        return;
      }
      console.info(
        "start_listening received after ASR ready; relying on automatic mic start.",
        { vendor: policy?.asr?.vendor?.primary ?? null }
      );
      // BUG FIX: actually start the mic when the server sends start_listening
      try {
        // Reuse the existing mic start pipeline so telemetry and state stay consistent
        const started = await safeStartRecorderStreaming(
          policy,
          "server.start_listening"
        );
        if (started) {
          const reason =
            (typeof frame?.reason === "string" && frame.reason) ||
            frame?.type ||
            "start_listening";
          _audioStreaming = true;
          setListeningState(true);
          emitConsoleBusEvent("client.ui_badge", { state: "Listening" });
          resetTurnStopFlag();
          resetSpeechFlag();
          __turnOpen = true;
          __turnOpenAt = Date.now();
          logStage("client.stream.on", { reason });
          await openTurnOnce(reason);
        }
      } catch (err) {
        try {
          console.warn("WSClient.startRecorderStreaming from start_listening failed", err);
        } catch (_) {}
      }
    } else if (frame.type === "stop_listening") {
      const rawStopReason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "stop_listening";
      if (_audioStreaming) {
        logStage("client.stream.off", { reason: rawStopReason });
      }
      _audioStreaming = false;
      await autoStopRecorder({ reason: rawStopReason }, {
        fallbackReason: "server_requested",
        source: "server.stop_listening",
        isAutoStop: true,
      });
      setAsrArmInFlight(false);
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: "server_requested" });
      resetTurnAudioContext();
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
      ensureTurnAudioReqId(frame?.policy || AppState?.policy || {});
      emitConsoleBusEvent("client.ui_badge", { state: "Listening" });
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.start";
      resetTurnStopFlag();
      resetSpeechFlag();
      __turnOpen = true;
      __turnOpenAt = Date.now();
      logStage("client.stream.on", { reason });
      await openTurnOnce(reason);
      await handleInputStartFrame(frame);  // now always starts mic
    } else if (frame.type === "input.stop") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.stop";
      if (_audioStreaming) {
        logStage("client.stream.off", { reason });
      }
      _audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
      emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      stopInputCapture({ reason: "input.stop" });
      resetTurnAudioContext();
    } else if (frame.type === "assistant.await_user") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "assistant.await_user";
      if (_audioStreaming) {
        logStage("client.stream.off", { reason });
      }
      _audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
      resetTurnAudioContext();
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
    hubLog: logStage,
    handleIncomingFrame,
  });

  frameParser = createFrameParser({
    hubLog: logStage,
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
    hubLog: logStage,
    recordLastError,
    DEFAULT_SUBPROTOCOLS,
    DEFAULT_CLOSE_REASON,
    TOKEN_EXPIRY_MS,
    getAudioStreaming: () => _audioStreaming,
    setAudioStreaming: (value) => { _audioStreaming = Boolean(value); },
    ensurePcmSender,
    resetAudioHeaderSent: () => resetTurnAudioContext(),
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
    endSession: sessionEnd,
  } = sessionManager;

  function requestSessionShutdown(reason = DEFAULT_CLOSE_REASON) {
    const normalizedReason = typeof reason === "string" && reason ? reason : DEFAULT_CLOSE_REASON;
    pendingCloseReason = normalizedReason;
    updateMicBaseEnabled(false, "mic_stop");
    resetClientTtsGate("client_shutdown");
    try {
      voicePhaseController.beginClosing(normalizedReason);
      syncAppStatePhase({ force: true });
    } catch (_) {}
    const closer = normalizedReason === DEFAULT_CLOSE_REASON ? sessionEnd : sessionClose;
    const result = closer(normalizedReason);
    const finalize = () => {
      socket = null;
      try { WSClient._ws = null; WSClient.ws = null; } catch {}
    };
    if (result && typeof result.then === "function") {
      return result.then((value) => {
        finalize();
        return value;
      });
    }
    finalize();
    return result;
  }

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
    hubLog: logStage,
    helpers: {
      startRecorderStreaming: safeStartRecorderStreaming,
      stopRecorder: performStopRecorder,
      stopInputCapture,
      handleInputStartFrame,
      clearPartialWatchdog,
      ensureTurnAudioReqId,
      sendAudioHeader,
      resetAudioHeaderSent: () => resetTurnAudioContext(),
      emitConsoleBusEvent,
      openTurnOnce,
      setWsPhase: connection && typeof connection.setWsPhase === "function"
        ? connection.setWsPhase
        : undefined,
      setWsConnected,
      setAsrArmInFlight,
      setListeningState,
      getSocket: () => socket,
      sendJson: (payload, opts = {}) => {
        if (connection && typeof connection.send === "function") {
          return connection.send(payload, { binary: false, ...(opts || {}) });
        }
        return false;
      },
    },
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
    try { WSClient._ws = ws || null; WSClient.ws = ws || null; } catch {}
    if (ws) {
      const protocol = typeof ws.protocol === "string" && ws.protocol ? truncateBannerString(ws.protocol, 48) : null;
      recordClientBannerEvent("ws.socket.open", protocol ? { protocol } : null);
    }
    flushClientBannerQueue();
  });

  WSClient.on("close", (event) => {
    socket = null;
    try { WSClient._ws = null; WSClient.ws = null; } catch {}
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
    try {
      const shutdownReason = pendingCloseReason || detailReason || closeCode || "ws_close";
      voicePhaseController.markClosed(shutdownReason);
      pendingCloseReason = null;
      syncAppStatePhase({ force: true });
    } catch (_) {
      pendingCloseReason = null;
    }
    if (detailReason) {
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: detailReason });
    }

    const transient = !detailReason
      || detailReason === "heartbeat_timeout"
      || closeCode === 1001
      || closeCode === 1006;

    if (frameParser && typeof frameParser.resetTtsGate === "function") {
      try {
        frameParser.resetTtsGate("ws.socket.close", { clearDescriptor: true });
      } catch (_) {}
    }

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

    try {
      logStage("client.input.capture", { source, hasPolicy });
    } catch {}

    try {
      const result = safeStartRecorderStreaming(policy, source);
      if (result && typeof result.catch === "function") {
        result.catch((err) => console.warn("startInputCapture failed", err));
      }
      return result;
    } catch (err) {
      console.warn("startInputCapture failed", err);
      return null;
    }
  }

  function stopInputCapture(options = {}) {
    const rawReason = options && typeof options === "object" && typeof options.reason === "string" && options.reason
      ? options.reason
      : "legacy_input";
    const normalizedReasonKey = String(rawReason || "").trim().toLowerCase();
    let fallbackReason = typeof options?.fallbackReason === "string" && options.fallbackReason
      ? options.fallbackReason
      : rawReason;
    if (!fallbackReason || fallbackReason === "legacy_input") {
      fallbackReason = "client_stop";
    } else if (fallbackReason === "input.stop") {
      fallbackReason = "server_requested";
    }
    const source = typeof options?.source === "string" && options.source
      ? options.source
      : "stop_input_capture";

    // On microphone acquisition errors, avoid cascading into recorder teardown paths
    // that might trigger a full websocket cleanup. Only send an input/turn stop signal.
    if (normalizedReasonKey.includes("gum") || normalizedReasonKey.includes("mic_gum_failure")) {
      try {
        logStage("client.input.stop.mic_gum_failure", {
          rawReason,
          fallbackReason,
          source,
          phase: getPhase?.() || null,
        });
      } catch (_) {}
      try {
        // This should send a clean "turn stop" style signal without forcing
        // a full session close. The server can decide whether to keep the
        // session alive after the mic failure.
        maybeSendTurnStop("mic_gum_failure");
      } catch (_) {}
      return;
    }

    try {
      const result = autoStopRecorder(rawReason, { fallbackReason, source, isAutoStop: true });
      if (result && typeof result.catch === "function") {
        result.catch((err) => console.warn("stopInputCapture failed", err));
      }
    } catch (err) {
      console.warn("stopInputCapture failed", err);
    }
  }

  async function handleInputStartFrame(frame) {
    const policy = frame?.policy || AppState?.policy || {};
    const reason = "input.start";
    try {
      const started = await safeStartRecorderStreaming(policy, reason);
      if (!started) {
        console.warn("handleInputStartFrame: startRecorderStreaming returned false");
        try {
          logStage("client.mic", {
            outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
            message: "startRecorderStreaming returned false from input.start",
          });
        } catch {}
      } else {
        try {
          logStage("client.mic", {
            outcome: MIC_OUTCOME.ARMED,
            message: "input.start → startRecorderStreaming",
          });
        } catch {}
      }
    } catch (err) {
      console.error("handleInputStartFrame mic start failed", err);
      try {
        logStage("client.mic", {
          outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
          message: err?.message || "input.start mic start failed",
        });
      } catch {}
    }
  }

  async function handleInputStopFrame(frame) {
    const rawReason = frame?.reason || "server_input_stop";
    const source = frame?.type || "input.stop";
    const fallbackReason = rawReason;

    try {
      logStage("client.input.stop_frame", {
        source,
        rawReason,
        fallbackReason,
        phase: getPhase?.() || null,
      });
    } catch (_) {}

    try {
      // Propagate the server-provided reason through to stopInputCapture so that
      // mic_gum_failure and similar cases can take the "no hard teardown" path.
      stopInputCapture({
        reason: rawReason,
        fallbackReason,
        source: "input.stop_frame",
      });
    } catch (err) {
      console.warn("handleInputStopFrame failed", err);
    }
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
    markConversationAsrReady("asr_ready_frame");
    conversationStartPlanned = true;
    try {
      ensureTurnAudioReqId(frame?.policy || AppState?.policy || {});
    } catch (_) {}
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

    // If the server asked for input and policy allows auto-record, start the recorder.
    try {
      const policy = AppState?.policy || {};
      const autoAfterGreet = policy?.auto_record_after_greet !== false;

      if (typeof safeStartRecorderStreaming === "function" && !_audioStreaming && autoAfterGreet) {
        logStage("client.mic", {
          outcome: MIC_OUTCOME.ARMED,
          message: "asr.ready → WSClient.startRecorderStreaming",
        });
        const result = safeStartRecorderStreaming(policy, "asr.ready");
        if (result && typeof result.catch === "function") {
          result.catch((err) => {
            console.warn("asr.ready mic start failed", err);
            try {
              logStage("client.mic", {
                outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
                message: err?.message || "asr.ready mic start failed",
              });
            } catch {}
          });
        }
      }
    } catch (err) {
      console.warn("asr.ready mic start failed", err);
      try {
        logStage("client.mic", {
          outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
          message: err?.message || "asr.ready mic start failed",
        });
      } catch {}
    }
    try {
      sendAudioKeepaliveNow();
    } catch (err) {
      console.warn("Immediate keepalive send failed after asr.ready", err);
    }
    try {
      enterConversationAfterGreet("asr.ready");
    } catch (_) {}
    return sanitized;
  }

  async function handleInfoFrame(frame) {
    const meta = frame && frame.meta;
    if (!meta || typeof meta.sid !== "string") {
      console.error("Invalid info frame", frame);
      await requestSessionShutdown("bad_info_frame");
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
    if (descriptor) {
      try {
        console.log("client.ws.tts_descriptor_frame", {
          type: frame.type,
          sampleRate: descriptor.sample_rate || descriptor.sampleRate || descriptor.rate_hz || null,
          channels: descriptor.channels || descriptor.channel_count || descriptor.num_channels || null,
        });
      } catch (_) {}
      if (frameParser && typeof frameParser.setTtsAudioDescriptor === "function") {
        frameParser.setTtsAudioDescriptor(descriptor);
      } else {
        const audioPlayer = getAudioPlayer();
        if (audioPlayer && typeof audioPlayer.setDescriptor === "function") {
          audioPlayer.setDescriptor(descriptor);
        }
      }
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
      resetTurnAudioContext();
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
      await requestSessionShutdown("resume_invalid");
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
  function arrayBufferToBase64(buffer) {
    if (!(buffer instanceof ArrayBuffer)) {
      return null;
    }
    if (typeof Buffer === "function" && typeof Buffer.from === "function") {
      try {
        return Buffer.from(buffer).toString("base64");
      } catch (err) {
        console.warn("arrayBufferToBase64 buffer conversion failed", err);
      }
    }
    const bytes = new Uint8Array(buffer);
    const chunkSize = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const chunk = bytes.subarray(i, Math.min(bytes.length, i + chunkSize));
      binary += String.fromCharCode(...chunk);
    }
    const encode = typeof btoa === "function"
      ? btoa
      : (typeof window !== "undefined" && typeof window.btoa === "function"
        ? window.btoa
        : null);
    if (encode) {
      return encode(binary);
    }
    return null;
  }

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
      return buf.arrayBuffer().then((buffer) => WSClient.sendAudioChunk(buffer, opts));
    }
    let arrayBuffer = null;
    if (buf instanceof ArrayBuffer) {
      arrayBuffer = buf;
    } else if (ArrayBuffer.isView(buf)) {
      arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    } else {
      arrayBuffer = toArrayBuffer(buf);
    }
    if (!arrayBuffer) {
      logTransportMisuse("sendAudioChunk_invalid_payload");
      try {
        logStage("client.audio_chunk_invalid_payload", {
          lane: typeof opts?.lane === "string" ? opts.lane : null,
          type: buf && buf.constructor ? buf.constructor.name : typeof buf,
        });
      } catch (_) {}
      console.error("sendAudioChunk: expected ArrayBuffer or TypedArray");
      return false;
    }
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (typeof options.lane === "undefined" || (typeof options.lane === "string" && !options.lane)) {
      options.lane = "mic";
    }
    const lane = typeof options.lane === "string" ? options.lane : "mic";
    if (lane === "mic") {
      maybePromoteReadyAfterMicAudio("mic_audio_frame");
      const now = Date.now();
      if (now < __pauseSendUntil) {
        const pauseMs = __pauseSendUntil - now;
        const ts = Number.isFinite(options.ts) ? Number(options.ts) : now;
        try { AppState?.hub?.log?.('client.audio.chunk_dropped_throttle', { ts, pause_ms: pauseMs }); } catch {}
        return true;
      }
    }
    const reqId = typeof options.reqId === "string" && options.reqId
      ? options.reqId
      : getCurrentTurnReqId();
    if (reqId) {
      options.reqId = reqId;
    } else {
      delete options.reqId;
    }
    const srHz = Number.isFinite(options.sampleRateHz) && options.sampleRateHz > 0
      ? Math.round(options.sampleRateHz)
      : (Number.isFinite(options.sampleRate) && options.sampleRate > 0
        ? Math.round(options.sampleRate)
        : null);
    if (srHz) {
      options.sampleRateHz = srHz;
      if (!Number.isFinite(options.sampleRate) || options.sampleRate <= 0) {
        options.sampleRate = srHz;
      }
    } else if (typeof options.sampleRateHz === "undefined") {
      options.sampleRateHz = AUDIO_HEADER_FRAME.sample_rate;
      if (!Number.isFinite(options.sampleRate) || options.sampleRate <= 0) {
        options.sampleRate = AUDIO_HEADER_FRAME.sample_rate;
      }
    }
    if (typeof options.keepalive !== "boolean") {
      delete options.keepalive;
    }
    if (!Number.isFinite(options.chunkCount) || options.chunkCount <= 0) {
      delete options.chunkCount;
    } else {
      options.chunkCount = Number(options.chunkCount);
    }
    options.lane = lane;
    const result = connection.sendBinary(arrayBuffer, options);
    if (result && typeof result.then === "function") {
      return result;
    }
    return result !== false;
  };

  WSClient.open = function wsClientOpen(options = {}, protocolsOverride) {
    resetClientTtsGate("session_restart");
    const ws = sessionOpen(options, protocolsOverride);
    socket = ws || null;
    if (!ws) {
      try { WSClient._ws = null; } catch {}
    } else {
      try { WSClient.ws = ws; } catch {}
      try {
        this.ws?.addEventListener?.("close", (ev) => {
          console.warn("client.ws outcome: close", { code: ev.code, reason: ev.reason });
        });
      } catch {}
    }
    return ws;
  };
  WSClient.close = function wsClientClose(reason) {
    return requestSessionShutdown(reason);
  };

  WSClient.endSession = function wsClientEndSession(reason) {
    return WSClient.close(typeof reason === "string" && reason ? reason : DEFAULT_CLOSE_REASON);
  };
  WSClient.send = function sendWs(payload, opts = {}) {
    const options = opts && typeof opts === "object" ? { ...opts, binary: false } : { binary: false };
    return connection.send(payload, options);
  };

  WSClient.inputStop = function inputStop(reason = "manual") {
    const frame = { type: "input.stop", reason };
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(frame)); // bypass phase gating for control frame
        this.log?.info?.("client.ws", { evt: "send", type: "input.stop", bypass: true, reason });
      } else {
        this.queue = this.queue || [];
        this.queue.push(frame);
      }
    } catch (e) {
      console.warn("input.stop send failed", e);
    }
  };

  WSClient.clearResume = function clearResume() {
    WSClient.resumeToken = null;
    try { sessionStorage?.removeItem?.("chibot.resumeToken"); } catch {}
  };

  // Fast-path for callers that use sendJSON() directly (audio.header, pings, etc.).
  (function wrapSendJSON() {
    const __origSendJSON = WSClient.sendJSON;
    WSClient.sendJSON = function sendJSONFast(frame) {
      try {
        const ws = socket || window.ws;
        const open = ws && ws.readyState === WebSocket.OPEN;
        const isControl = typeof connection?.isControlFrame === "function"
          ? connection.isControlFrame(frame)
          : false;
        if (open && isControl) {
          const codec = getNegotiatedControlCodec();
          const encoded = typeof connection?.encodeControlFramePayload === "function"
            ? connection.encodeControlFramePayload(frame, codec)
            : null;
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
  WSClient.safeStartRecorderStreaming = function wsClientSafeStartRecorderStreaming(policy = {}, source = "manual") {
    return safeStartRecorderStreaming(policy, source);
  };
  WSClient.safeRequestAsrOpen = function wsClientSafeRequestAsrOpen(reason) {
    return safeRequestAsrOpen(reason);
  };
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
      const started = await safeStartRecorderStreaming(AppState?.policy || {}, "self_test");
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
      resetTurnAudioContext();
      resetTurnIntent("ws.close");
    });
    window.addEventListener("ws.resume_invalid", () => {
      resetTurnAudioContext();
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
