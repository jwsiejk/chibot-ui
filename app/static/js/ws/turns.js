// app/static/js/ws/turns.js
// Encapsulates turn state, ASR control, and "can capture now?" logic.

const DEFAULT_ASR_VENDOR = "gcp";
const HARD_ASR_CLOSE_REASONS = new Set([
  "user_requested",
  "user_restart",
  "user_end",
  "client_stop",
  "client_shutdown",
  "end_button",
  "server_requested",
  "server_error",
  "resume_invalid",
  "asr_unavailable",
]);

const DEFAULT_MIC_OUTCOME = {
  PERM_GRANTED: "perm_granted",
  ARMED: "armed",
  STREAMING: "streaming",
  STREAMING_HEARTBEAT: "streaming_heartbeat",
  STOPPED: "stopped",
  ERROR_DENIED: "error_denied",
  ERROR_NO_DEVICE: "error_no_device",
  ERROR_GUM: "error_getuser_media",
  ERROR_SILENT: "error_silent_stream",
  ERROR_WS_SEND: "error_ws_send",
  ERROR_STATE_GUARD: "error_state_guard",
  ERROR_SENDER_INIT: "error_sender_init",
  ERROR_UNKNOWN: "error_unknown",
};

function now() {
  return Date.now();
}

function dbg(key, fallback = false) {
  try {
    return !!(window.AppState?.debug && window.AppState.debug[key]);
  } catch (err) {
    void err;
    return fallback;
  }
}

function clone(value) {
  if (Array.isArray(value)) {
    return value.slice();
  }
  if (value && typeof value === "object") {
    return { ...value };
  }
  return value;
}

export function createTurnRuntime(config = {}) {
  const {
    AppState = {},
    policyRuntime = {},
    audioRuntime = {},
    connection = {},
    telemetry = {},
    hubLog: providedHubLog = () => {},
    setSenderPauseReason: providedSetSenderPauseReason,
    applySenderPausedState: providedApplySenderPausedState,
  } = config;

  const helpers = config && typeof config === "object" && typeof config.helpers === "object"
    ? config.helpers
    : {};

  const hubLogger = typeof providedHubLog === "function" ? providedHubLog : () => {};
  const logStage = typeof telemetry?.logStage === "function" ? telemetry.logStage : () => {};
  const logMic = typeof telemetry?.logMic === "function" ? telemetry.logMic : () => {};
  const recordClientBannerEvent = typeof telemetry?.recordClientBannerEvent === "function"
    ? telemetry.recordClientBannerEvent
    : () => {};
  const MIC_OUTCOME = telemetry?.MIC_OUTCOME && typeof telemetry.MIC_OUTCOME === "object"
    ? { ...DEFAULT_MIC_OUTCOME, ...telemetry.MIC_OUTCOME }
    : DEFAULT_MIC_OUTCOME;

  const {
    shouldAutoRearmAfterClosed = () => false,
    getClientVadPolicyRoot = () => ({ vad: { client: {} } }),
    getCurrentPolicy = () => (AppState?.policy && typeof AppState.policy === "object"
      ? AppState.policy
      : {}),
  } = policyRuntime || {};

  const {
    primeAsrStreamFromRing = () => {},
    getPcmRing = () => null,
    updatePcmSenderState = () => {},
  } = audioRuntime || {};

  const {
    startRecorderStreaming = async () => true,
    stopRecorder = async () => false,
    stopInputCapture = () => {},
    handleInputStartFrame = async () => {},
    clearPartialWatchdog = () => {},
    sendAudioHeader = () => {},
    resetAudioHeaderSent = () => {},
    emitConsoleBusEvent = () => {},
    openTurnOnce = async () => true,
    waitForOnce = async () => null,
    setWsPhase: externalSetWsPhase,
    setWsConnected: externalSetWsConnected,
    setAsrArmInFlight: externalSetAsrArmInFlight,
    setListeningState: externalSetListeningState,
    getSocket = () => null,
  } = helpers || {};

  const sendJson = typeof helpers?.sendJson === "function"
    ? helpers.sendJson
    : (payload, opts = {}) => {
      if (typeof connection?.send === "function") {
        return connection.send(payload, { binary: false, ...opts });
      }
      return false;
    };

  const ASR_RATE = Number.isFinite(AppState?.targetSampleRate) ? AppState.targetSampleRate : 16000;

  let turnOpen = false;
  let turnOpenAt = 0;
  let audioStreaming = false;
  let awaitingAsrClosedAck = false;
  let pendingAsrClosedSeq = null;
  let awaitingTurnEndForRearm = false;
  let pendingRearmReason = null;
  let asrRecovering = false;
  const primedSessionIds = new Set();
  let warmupUntil = 0;
  let firstChunkSeen = false;
  let armingGraceUntil = 0;
  const senderPauseReasons = new Set();
  let senderPaused = false;

  function updateState(patch) {
    if (!patch || typeof patch !== "object") {
      return;
    }
    try {
      if (typeof AppState?.setState === "function") {
        AppState.setState(patch);
        return;
      }
    } catch (err) {
      console.warn("turns.updateState setState failed", err);
    }
    try {
      Object.assign(AppState || {}, patch);
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
      console.warn("turns.setAppStateValue failed", err);
      try {
        updateState({ [key]: value });
      } catch {}
    }
  }

  function setWsPhase(phase) {
    if (typeof externalSetWsPhase === "function") {
      externalSetWsPhase(phase);
      return;
    }
    if (typeof phase !== "string" || !phase) {
      return;
    }
    setAppStateValue("wsPhase", phase);
  }

  function setWsConnected(connected) {
    if (typeof externalSetWsConnected === "function") {
      externalSetWsConnected(connected);
      return;
    }
    setAppStateValue("wsConnected", Boolean(connected));
  }

  function setAsrReadyFlag(ready, meta = {}) {
    const desired = Boolean(ready);
    const prev = typeof AppState?.asrReady === "boolean" ? AppState.asrReady : null;
    const vendor = meta && Object.prototype.hasOwnProperty.call(meta, "vendor")
      ? meta.vendor
      : (AppState?.asrVendor ?? null);
    AppState.asrReady = desired;
    const patch = desired
      ? { asrReady: true, asrVendor: vendor }
      : { asrReady: false, asrVendor: null };
    updateState(patch);
    try {
      logStage("client.asr.state", { ready: desired, prev, vendor });
    } catch {}
    try {
      console.log("client.asr.state", { ready: desired, prev, vendor });
    } catch {}
  }

  function setAsrArmInFlight(inFlight) {
    if (typeof externalSetAsrArmInFlight === "function") {
      externalSetAsrArmInFlight(inFlight);
      return;
    }
    setAppStateValue("asrArmInFlight", Boolean(inFlight));
  }

  function setListeningState(active) {
    if (typeof externalSetListeningState === "function") {
      externalSetListeningState(active);
      return;
    }
    const listening = Boolean(active);
    updateState({ listening });
    if (!listening && (AppState?.wsConnected || AppState?.connectionState === "connected")) {
      setWsPhase("connected");
    }
    try {
      updatePcmSenderState();
    } catch {}
  }

  function beginWarmup(ms = 1200) {
    const delay = Number.isFinite(ms) ? Math.max(0, ms) : 0;
    warmupUntil = now() + delay;
  }

  function warming() {
    return now() < warmupUntil;
  }

  const applySenderPausedState = typeof providedApplySenderPausedState === "function"
    ? providedApplySenderPausedState
    : function applySenderPausedState() {
      const nextPaused = senderPauseReasons.size > 0;
      if (senderPaused === nextPaused) {
        return;
      }
      senderPaused = nextPaused;
      setAppStateValue("senderPaused", senderPaused);
      try {
        updatePcmSenderState();
      } catch {}
    };

  const setSenderPauseReason = typeof providedSetSenderPauseReason === "function"
    ? providedSetSenderPauseReason
    : function setSenderPauseReason(reason, value) {
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
    };

  function syncSenderPaused(value) {
    setSenderPauseReason("legacy", value);
  }

  function normalizeReason(reason) {
    if (typeof helpers?.normalizeReason === "function") {
      return helpers.normalizeReason(reason);
    }
    if (typeof reason === "string" && reason) {
      return reason;
    }
    if (reason && typeof reason === "object" && typeof reason.reason === "string" && reason.reason) {
      return reason.reason;
    }
    return "unspecified";
  }

  function getClientVadPolicySnapshot() {
    try {
      const root = getClientVadPolicyRoot();
      if (root && typeof root === "object") {
        return clone(root);
      }
    } catch {}
    return { vad: { client: {} } };
  }

  function getWarmupMs() {
    try {
      const snapshot = getClientVadPolicySnapshot();
      const candidate = snapshot?.vad?.client?.warmup_ms;
      if (Number.isFinite(candidate) && candidate >= 0 && candidate <= 10000) {
        return candidate;
      }
    } catch {}
    return 1200;
  }

  function clearPendingRearm() {
    awaitingTurnEndForRearm = false;
    pendingRearmReason = null;
  }

  function resetTurnIntent(reason) {
    if (!turnOpen) {
      return;
    }
    turnOpen = false;
    turnOpenAt = 0;
    try {
      hubLogger("client.turn.intent", { action: "close", reason: reason || "reset" });
    } catch {}
  }

  function canCaptureNow() {
    if (dbg("audio_safe_mode") || dbg("force_capture")) {
      return true;
    }
    if (!firstChunkSeen || now() < armingGraceUntil) {
      return true;
    }
    let socket = null;
    try {
      socket = getSocket();
    } catch {}
    if (socket && typeof socket.readyState === "number") {
      if (socket.readyState !== WebSocket.OPEN) {
        return false;
      }
    } else {
      const snapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
      if (snapshot && typeof snapshot.wsConnected === "boolean" && !snapshot.wsConnected) {
        return false;
      }
    }
    const state = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
    if (state?.tts || state?.ttsActive) {
      return false;
    }
    return warming() || (Boolean(state?.listening) && !senderPaused);
  }

  function buildAsrOpenPayload(options = {}) {
    const payload = { type: "asr.open" };
    if (typeof options.vendor === "string" && options.vendor) {
      payload.vendor = options.vendor;
    }
    if (Number.isFinite(options.sample_rate)) {
      payload.sample_rate = Number(options.sample_rate);
    }
    if (typeof options.language === "string" && options.language) {
      payload.language = options.language;
    }
    if (typeof options.reason === "string" && options.reason) {
      payload.reason = options.reason;
    }
    if (options && typeof options.metadata === "object" && options.metadata) {
      payload.metadata = { ...options.metadata };
    }
    if (typeof options.recover === "boolean") {
      payload.recover = options.recover;
    }
    return payload;
  }

  function openAsr(opts = {}) {
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (!options.recover) {
      const ring = getPcmRing();
      if (ring && typeof ring.clear === "function") {
        try {
          ring.clear();
        } catch (err) {
          try {
            console.warn("turns.pcmRing.clear failed", err);
          } catch {}
        }
      }
      if (primedSessionIds.size) {
        primedSessionIds.clear();
      }
      AppState._recoverPrimePending = false;
    }
    const payload = buildAsrOpenPayload(options);
    try {
      logStage("client.asr_open_request", { reason: options.reason || "unspecified" });
    } catch {}
    return sendJson(payload, { binary: false });
  }

  function requestAsrArm(reason) {
    const label = normalizeReason(reason);
    const snapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
    const ttsActive = Boolean(snapshot?.tts || snapshot?.ttsActive);
    if (ttsActive) {
      awaitingTurnEndForRearm = true;
      pendingRearmReason = label || "tts_active";
      try {
        logStage("client.asr_rearm_deferred", { reason: pendingRearmReason, ttsActive: true });
      } catch {}
      return;
    }
    const reasonLabel = pendingRearmReason || label;
    clearPendingRearm();
    try {
      setAsrArmInFlight(true);
      logStage("client.asr_rearm_request", { reason: reasonLabel });
      openAsr({ reason: reasonLabel });
      setWsPhase("arming");
    } catch (err) {
      setAsrArmInFlight(false);
      const phase = AppState.wsConnected ? "connected" : "disconnected";
      setWsPhase(phase);
      console.error("Failed to send asr.open", err);
      logStage("client.mic", { outcome: MIC_OUTCOME.ERROR_WS_SEND, message: err?.message });
    }
  }

  function isHardAsrCloseReason(reason) {
    if (typeof reason !== "string" || !reason) {
      return false;
    }
    return HARD_ASR_CLOSE_REASONS.has(reason.toLowerCase());
  }

  async function requestAsrClose(reason = "client_stop") {
    const label = normalizeReason(reason);
    const normalizedLabel = typeof label === "string" && label
      ? label.toLowerCase()
      : "unspecified";
    setAsrArmInFlight(false);
    const seq = (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
      ? crypto.randomUUID()
      : `${now()}_${Math.random().toString(16).slice(2)}`;
    const sid = typeof AppState?.asrSid === "string" && AppState.asrSid ? AppState.asrSid : null;
    const payload = { type: "asr.close", seq, reason: label };
    if (sid) {
      payload.sid = sid;
    }
    let ack = null;
    try {
      sendJson(payload, { binary: false });
      pendingAsrClosedSeq = seq;
      awaitingAsrClosedAck = true;
      logStage("client.asr_close_request", { reason: label });
    } catch (err) {
      pendingAsrClosedSeq = null;
      awaitingAsrClosedAck = false;
      console.warn("Failed to send asr.close", err);
    }
    if (awaitingAsrClosedAck) {
      try {
        ack = await waitForOnce(
          "asr.closed",
          (frame) => frame?.seq === seq || (!!sid && frame?.sid === sid),
          2000,
        );
      } catch (err) {
        console.warn("asr.closed ack timeout; proceeding cautiously", err);
      } finally {
        awaitingAsrClosedAck = false;
        pendingAsrClosedSeq = null;
      }
    }
    if (isHardAsrCloseReason(normalizedLabel)) {
      await stopRecorder(label);
    } else {
      const pauseLabel = typeof label === "string" && label ? label : "turn_completed";
      setSenderPauseReason(pauseLabel, true);
      applySenderPausedState();
    }
    return ack;
  }

  async function recoverFromAsrFault(reason) {
    if (asrRecovering) {
      return;
    }
    asrRecovering = true;
    const label = typeof reason === "string" && reason ? reason : "unknown";

    if (label === "partial_timeout") {
      clearPartialWatchdog();
      try {
        console.warn("ASR partial watchdog fired; skipping auto-recover", { reason: label });
        logStage?.("client.asr_recover_skipped", { reason: label });
      } catch {}
      asrRecovering = false;
      return;
    }

    clearPartialWatchdog();
    try {
      await requestAsrClose(`recover:${label}`);
    } catch (err) {
      try {
        console.warn("ASR recovery close failed", err);
      } catch {}
    }
    try {
      await openAsr({
        vendor: AppState?.asrVendor || DEFAULT_ASR_VENDOR,
        sample_rate: ASR_RATE,
        language: AppState?.language || "en-US",
        recover: true,
      });
    } catch (err) {
      try {
        console.warn("ASR recovery open failed", err);
      } catch {}
    }
    try {
      const readyFrame = await waitForOnce("asr.ready", () => true, 2000);
      const sid = readyFrame?.sid || AppState?.asrSid || `${now()}`;
      primeAsrStreamFromRing(sid);
      AppState._recoverPrimePending = false;
    } catch (err) {
      AppState._recoverPrimePending = true;
      try {
        console.warn("ASR recovery wait_for_ready failed", err);
      } catch {}
    } finally {
      asrRecovering = false;
    }
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
    if (hasInputField) {
      safe.input = input;
    }
    return safe;
  }

  function handleAsrReadyFrame(frame) {
    const sanitized = sanitizeAsrReadyFrame(frame);
    if (typeof sanitized.sid === "string" && sanitized.sid) {
      AppState.asrSid = sanitized.sid;
    } else if (frame && typeof frame.sid === "string" && frame.sid) {
      AppState.asrSid = frame.sid;
    }
    try {
      window.dispatchEvent(new CustomEvent("asr.ready"));
    } catch {}
    AppState.asrVendor = sanitized.vendor || DEFAULT_ASR_VENDOR;
    setAsrReadyFlag(true, { vendor: AppState.asrVendor });
    beginWarmup(getWarmupMs());
    try {
      updatePcmSenderState();
    } catch {}
    try {
      window.requestAnimationFrame(() => window.AppUI?.refresh?.());
    } catch {}
    if (typeof AppState.emit === "function") {
      try {
        AppState.emit("asrReady", {
          ready: true,
          vendor: AppState.asrVendor,
          input: sanitized.input ?? null,
        });
      } catch {}
    }
    try {
      logStage("client.asr", {
        stage: "ready",
        vendor: AppState.asrVendor,
        input: sanitized.input ?? null,
      });
    } catch {}
    return sanitized;
  }

  async function handleAsrStateFrame(frame) {
    if (!frame || typeof frame !== "object") {
      return;
    }

    if (frame.type === "asr.error" || frame.type === "asr.closed" || frame.type === "asr.reset") {
      clearPartialWatchdog();
      resetAudioHeaderSent();
      if (frame.type === "asr.closed" || frame.type === "asr.reset") {
        setAsrReadyFlag(false, { vendor: null });
      }
      if (frame.type === "asr.closed") {
        AppState.asrSid = null;
        awaitingAsrClosedAck = false;
        pendingAsrClosedSeq = null;
        clearPendingRearm();
        const status = typeof frame?.status === "string" && frame.status ? frame.status : "closed";
        const reasonRaw = typeof frame?.reason === "string" && frame.reason ? frame.reason : "";
        const normalizedReason = normalizeReason(reasonRaw || "asr_closed");
        const shouldRearm = shouldAutoRearmAfterClosed(normalizedReason);
        if (status !== "already_closed" && shouldRearm) {
          const allowCaptureDuringTts = AppState?.policy?.audio?.allow_capture_during_tts;
          if (allowCaptureDuringTts === false) {
            awaitingTurnEndForRearm = true;
            pendingRearmReason = normalizedReason || "asr_closed";
          } else {
            requestAsrArm(normalizedReason || "asr_closed");
          }
        }
        audioStreaming = false;
        setListeningState(false);
        resetTurnIntent(frame?.type || "asr.closed");
        emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      }
      return;
    }

    if (frame.type === "asr.ready") {
      if (typeof frame.sid === "string" && frame.sid) {
        AppState.asrSid = frame.sid;
      }
      const readyFrame = handleAsrReadyFrame(frame) || frame;
      setAsrArmInFlight(false);
      setWsConnected(true);
      setWsPhase("ready");
      emitConsoleBusEvent("client.asr.ready", { asrReady: true });
      const startReason = "asr_ready_forced_start";
      hubLogger("client.ws_ready_check", {
        socketOpen: (() => {
          const socket = getSocket();
          return !!socket && socket.readyState === WebSocket.OPEN;
        })(),
        phase: (AppState?.wsPhase || AppState?.connectionState || null),
      });
      try {
        await openTurnOnce(startReason);
      } catch {}
      try {
        const started = await startRecorderStreaming(frame?.policy || {}, startReason);
        if (started) {
          audioStreaming = true;
        }
      } catch (err) {
        console.warn("auto-arm on asr.ready failed", err);
      }
      try {
        const capturePolicy = AppState?.policy?.capture || {};
        const mode = typeof capturePolicy?.mode === "string" && capturePolicy.mode
          ? capturePolicy.mode
          : "webrtc_aec";
        const ctxRate = window.__audioCtx && typeof window.__audioCtx.sampleRate === "number"
          ? window.__audioCtx.sampleRate
          : 16000;
        emitConsoleBusEvent("client.capture.mode", { mode, ctxSampleRate: ctxRate });
      } catch {}
      logStage("diag", { label: "asr.ready" });
      logStage("client.asr_arm_clear", { vendor: AppState.asrVendor || DEFAULT_ASR_VENDOR });
      try {
        sendAudioHeader(frame);
      } catch {}
      if (AppState._recoverPrimePending) {
        const sid = readyFrame?.sid || AppState?.asrSid || `${now()}`;
        primeAsrStreamFromRing(sid);
        AppState._recoverPrimePending = false;
      }
      return;
    }

    if (frame.type === "asr.unavailable") {
      const reason = typeof frame?.reason === "string" ? frame.reason : "";
      const details = typeof frame?.details === "string"
        ? frame.details
        : (typeof frame?.detail === "string" ? frame.detail : "");
      console.warn("asr.unavailable", reason, details);
      AppState.asrVendor = null;
      setAsrReadyFlag(false, { vendor: null });
      updatePcmSenderState();
      await stopRecorder("asr_unavailable");
      resetAudioHeaderSent();
      resetTurnIntent(frame?.type || "asr.unavailable");
      setAsrArmInFlight(false);
      if (typeof AppState.emit === "function") {
        try {
          AppState.emit("asrReady", { ready: false, reason, vendor: null });
        } catch {}
      }
      try {
        const hud = window?.HUD || window?.DiagHUD || window?.DiagHud;
        hud?.setState?.("Chat");
      } catch (err) {
        console.warn("Failed to update HUD state after asr.unavailable", err);
      }
      try {
        const view = window.TranscriptView;
        view?.showSystemFromChip?.(
          "Sorry, having issues hearing you right now, but I can absolutely still assist via chat.",
        );
      } catch (err) {
        console.warn("Failed to render Chip system message after asr.unavailable", err);
      }
      try {
        window?.Banner?.show?.(
          "Voice temporarily unavailable. You can continue via chat.",
          { level: "warning", ttlMs: 10000 },
        );
      } catch (err) {
        console.warn("Failed to show voice unavailable banner", err);
      }
      return;
    }

    if (frame.type === "asr.turn") {
      const begin = frame.state === "begin";
      if (dbg("audio_safe_mode") && begin && !AppState?.listening) {
        try {
          const turned = await openTurnOnce("safe_turn_begin");
          if (!turned) {
            console.warn("safe_mode turn autostart skipped: turn not open");
          } else {
            const started = await startRecorderStreaming(AppState?.policy || {}, "safe_turn_begin");
            if (!started) {
              console.warn("safe_mode turn autostart recorder returned false");
            }
          }
        } catch (err) {
          console.warn("safe_mode turn autostart failed", err);
        }
      }
      try {
        window.UIState = window.UIState || {};
        window.UIState.asrTurnActive = begin;
        if (window.StatusBar && typeof window.StatusBar.render === "function") {
          window.StatusBar.render({
            ...window.UIState,
            policy: (window.AppState?.policy || {}),
          });
        }
      } catch (err) {
        console.warn("asr.turn handling error", err);
      }
      try {
        if (typeof AppState.setState === "function") {
          AppState.setState({ asrTurnActive: begin });
        }
      } catch {}
      if (!begin) {
        resetTurnIntent(frame?.state || "turn.end");
      }
      return;
    }

    if (frame.type === "turn.begin") {
      try {
        if (typeof AppState.setState === "function") {
          AppState.setState({ turnActive: true });
        }
      } catch {}
      updatePcmSenderState();
      try {
        window.dispatchEvent(new CustomEvent("turn.begin", { detail: frame }));
      } catch {}
      return;
    }

    if (frame.type === "turn.end") {
      try {
        if (typeof AppState.setState === "function") {
          AppState.setState({ turnActive: false });
        }
      } catch {}
      updatePcmSenderState();
      try {
        window.dispatchEvent(new CustomEvent("turn.end", { detail: frame }));
      } catch {}
      if (awaitingTurnEndForRearm) {
        const reason = pendingRearmReason || "turn_end_rearm";
        clearPendingRearm();
        if (shouldAutoRearmAfterClosed(reason)) {
          requestAsrArm(reason);
        }
      }
      return;
    }

    if (frame.type === "asr.timeout") {
      void recoverFromAsrFault("timeout");
      return;
    }

    if (frame.type === "input.start") {
      audioStreaming = true;
      setListeningState(true);
      emitConsoleBusEvent("client.ui_badge", { state: "Listening" });
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.start";
      turnOpen = true;
      turnOpenAt = now();
      hubLogger("client.stream.on", { reason });
      await openTurnOnce(reason);
      await handleInputStartFrame(frame);
      return;
    }

    if (frame.type === "input.stop") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "input.stop";
      if (audioStreaming) {
        hubLogger("client.stream.off", { reason });
      }
      audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
      emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      stopInputCapture({ reason: "input.stop" });
      resetAudioHeaderSent();
      return;
    }

    if (frame.type === "assistant.await_user") {
      const reason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "assistant.await_user";
      if (audioStreaming) {
        hubLogger("client.stream.off", { reason });
      }
      audioStreaming = false;
      setListeningState(false);
      resetTurnIntent(reason);
      return;
    }

    if (frame.type === "stop_listening") {
      const rawStopReason = typeof frame?.reason === "string" && frame.reason
        ? frame.reason
        : frame?.type || "stop_listening";
      const stopReason = normalizeReason(rawStopReason);
      const hardStopReason = isHardAsrCloseReason(stopReason)
        ? stopReason
        : "server_requested";
      if (audioStreaming) {
        hubLogger("client.stream.off", { reason: hardStopReason });
      }
      audioStreaming = false;
      await stopRecorder({ reason: hardStopReason }, {
        fallbackReason: hardStopReason,
        source: "server.stop_listening",
      });
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
          const reason = (typeof frame?.reason === "string" && frame.reason) || "server_requested";
          window.dispatchEvent(new CustomEvent("stop_listening", { detail: { reason } }));
        } catch (err) {
          console.warn("stop_listening event dispatch failed", err);
        }
      }
      return;
    }
  }

  return {
    resetTurnIntent,
    canCaptureNow,
    openAsr,
    requestAsrArm,
    requestAsrClose,
    recoverFromAsrFault,
    handleAsrStateFrame,

    // Internal helpers exposed for future integration/testing.
    _setSenderPauseReason: setSenderPauseReason,
    _syncSenderPaused: syncSenderPaused,
    _beginWarmup: beginWarmup,
    _setFirstChunkSeen(value) {
      firstChunkSeen = Boolean(value);
    },
    _setArmingGraceUntil(ts) {
      armingGraceUntil = Number.isFinite(ts) ? ts : 0;
    },
    _setAudioStreaming(value) {
      audioStreaming = Boolean(value);
    },
  };
}
