// app/static/js/audio/capture_runtime.js
// Encapsulates VAD+AppState bridge, consoleBus publishing, silence timers,
// and recorder start/stop lifecycle for AskChip.

import { logMic as telemetryLogMic, logStage as telemetryLogStage } from "../ws/telemetry.js";

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

const normalizeReason = (reason) => {
  if (typeof reason === "string" && reason) {
    return reason;
  }
  if (reason && typeof reason === "object" && typeof reason.reason === "string" && reason.reason) {
    return reason.reason;
  }
  return "unspecified";
};

const toReasonKey = (value) => {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim().toLowerCase();
  }
  if (typeof value === "object" && typeof value.reason === "string" && value.reason) {
    return value.reason.trim().toLowerCase();
  }
  return String(value).trim().toLowerCase();
};

const reasonLooksLikeVadOrMic = (value) => {
  const key = toReasonKey(value);
  if (!key) {
    return false;
  }
  return VAD_OR_MIC_REASON_PATTERNS.some((pattern) => pattern.test(key));
};

export const reasonLooksUserInitiated = (value) => {
  const key = toReasonKey(value);
  if (!key) {
    return false;
  }
  return USER_INITIATED_STOP_REASONS.has(key);
};

export const reasonLooksServerError = (value) => {
  const key = toReasonKey(value);
  if (!key) {
    return false;
  }
  if (SERVER_ERROR_STOP_REASONS.has(key)) {
    return true;
  }
  return SERVER_ERROR_REASON_PATTERNS.some((pattern) => pattern.test(key));
};

const toReasonLabel = (value) => {
  const label = normalizeReason(value);
  return typeof label === "string" ? label : "unspecified";
};

export function createCaptureRuntime({
  AppState,
  policyRuntime,          // createPolicyRuntime(AppState)
  audioRuntime,           // createWsAudioRuntime(...)
  initVAD,                // existing initVAD(...) function
  consoleBus,             // existing console event bus
  hubLog,                 // logging helper
  logStage: providedLogStage,               // telemetry logStage from ws/telemetry.js
  recordClientBannerEvent, // telemetry banner from ws/telemetry.js
  schedulePartialWatchdog = null,
  clearPartialWatchdog = null,
  resetTurnIntent = null,
  MIC_OUTCOME = {},
  onVadSilenceStop = null,
  canAutoStopFromVad = null,
  onCaptureStop = null,
}) {
  const MAX_GATE_SILENCE_MS = 3000;
  const VAD_SILENCE_TIMEOUT_SAMPLE_RATE = 10;
  const VAD_APPSTATE_KEYS = [
    "vadActive",
    "vadSpeech",
    "vadConfidence",
    "vadEnergyDb",
    "vadNoiseDb",
  ];

  const logStage = providedLogStage || telemetryLogStage;
  const logMic = telemetryLogMic;

  const DEFAULT_CAPTURE_CONSTRAINTS = {
    audio: {
      channelCount: 1,
      sampleRate: 16000,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };

  const {
    getClientVadPolicyRoot = () => ({}),
  } = policyRuntime || {};

  const {
    getPcmRing = () => null,
    updatePcmSenderState = () => {},
    ensurePcmSender = async () => null,
    resetSilenceSuppression = () => {},
    scheduleAudioKeepalive = () => {},
    clearAudioKeepaliveTimer = () => {},
  } = audioRuntime || {};

  const senderPauseReasons = new Set();
  let senderPaused = false;
  let audioStreaming = false;
  let pcmSenderReady = false;
  let pcmSenderReadyLogged = false;
  let firstChunkSeen = false;
  let armingGraceUntil = 0;
  let micRecordingStartAt = null;
  let captureStream = null;
  // ===== Internal VAD state =====
  let vadController = null;
  let vadSilenceTimerId = null;

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

  const resolvePhase = () => (typeof AppState?.phase === "string" ? AppState.phase : null);

  function summarizeConstraints(value) {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value !== "object") {
      return value;
    }
    const audio = value && typeof value.audio === "object" && !Array.isArray(value.audio)
      ? value.audio
      : null;
    const audioSummary = audio
      ? {
          channelCount: audio.channelCount ?? null,
          deviceId: audio.deviceId ?? null,
          sampleRate: audio.sampleRate ?? null,
          echoCancellation: audio.echoCancellation ?? null,
          noiseSuppression: audio.noiseSuppression ?? null,
          autoGainControl: audio.autoGainControl ?? null,
        }
      : value?.audio ?? null;
    return { audio: audioSummary };
  }

  function buildConstraintsFromPolicy(policy) {
    const capture = policy && typeof policy === "object" && policy.capture && typeof policy.capture === "object"
      ? policy.capture
      : null;
    if (capture && capture.constraints && typeof capture.constraints === "object") {
      return capture.constraints;
    }
    return DEFAULT_CAPTURE_CONSTRAINTS;
  }

  function ensureStreamProvider(stream) {
    if (typeof audioRuntime?.setCaptureStreamProvider === "function") {
      try {
        audioRuntime.setCaptureStreamProvider(() => stream);
      } catch (err) {
        console.warn("capture_runtime: setCaptureStreamProvider failed", err);
      }
    }
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
    try {
      if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
        window.requestAnimationFrame(() => {
          try {
            if (typeof window.AppUI?.refresh === "function") {
              window.AppUI.refresh();
            }
          } catch {}
        });
      }
    } catch {}
    try {
      updatePcmSenderState();
    } catch {}
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

  function resolveConsoleBusFunction() {
    if (typeof consoleBus === "function") {
      return consoleBus;
    }
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

  // ===== VAD API =====
  function getVadController() {
    return vadController;
  }

  function setVadAppState(partial) {
    if (!partial || typeof partial !== "object") {
      return;
    }
    const sanitized = {};
    for (const key of VAD_APPSTATE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(partial, key)) {
        sanitized[key] = partial[key];
        if (AppState && typeof AppState === "object") {
          AppState[key] = partial[key];
        }
      }
    }
    const keys = Object.keys(sanitized);
    if (!keys.length) {
      return;
    }
    updateState(sanitized);
  }

  function publishVad(event, payload) {
    if (typeof event !== "string" || !event) {
      return;
    }
    if (event === "client.vad.speech_start") {
      clearVadSilenceTimer();
      emitConsoleBusEvent("client.vad.start_speech");
      let ring = null;
      try {
        ring = getPcmRing();
      } catch {}
      if (typeof ring?.clear === "function") {
        try {
          ring.clear();
        } catch (err) {
          try { console.warn("pcmRing.clear failed", err); } catch (_) {}
        }
      }
      try {
        if (typeof schedulePartialWatchdog === "function") {
          schedulePartialWatchdog("vad_speech_start");
        }
      } catch {}
    } else if (event === "client.vad.speech_end") {
      const durationValue = Number(payload && payload.duration_ms);
      const durationMs = Number.isFinite(durationValue) ? Math.max(0, Math.round(durationValue)) : null;
      const detail = durationMs !== null ? { duration_ms: durationMs } : undefined;
      emitConsoleBusEvent("client.vad.end_speech", detail);
      scheduleVadSilenceTimer();
      try {
        if (typeof clearPartialWatchdog === "function") {
          clearPartialWatchdog();
        }
      } catch {}
    }
    hubLog(event, payload);
  }

  function handleVadGateChange(state) {
    if (state === "pause") {
      try { syncSenderPaused(true); } catch {}
    } else if (state === "resume") {
      try { syncSenderPaused(false); } catch {}
    }
    try {
      const action = state;
      hubLog("client.vad.gate", { action, state, senderPaused: AppState?.senderPaused });
    } catch {}
  }

  function getClientVadPolicyConfig() {
    try {
      const root = typeof getClientVadPolicyRoot === "function" ? getClientVadPolicyRoot() : null;
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

  function scheduleVadSilenceTimer() {
    const timeoutMs = getVadSilenceTimeoutMs();
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return;
    }
    clearVadSilenceTimer();
    vadSilenceTimerId = setTimeout(() => {
      vadSilenceTimerId = null;
      emitConsoleBusEvent("client.vad.silence_timeout", undefined, VAD_SILENCE_TIMEOUT_SAMPLE_RATE);
      const okToAutoStop = typeof canAutoStopFromVad === "function" ? !!canAutoStopFromVad() : false;
      if (!okToAutoStop) {
        // Pre-speech silence; keep listening.
        try { console.info("VAD silence timer fired pre-speech; not auto-stopping"); } catch {}
        return;
      }

      if (typeof onVadSilenceStop === "function") {
        try {
          onVadSilenceStop("vad_silence_timeout");
        } catch (err) {
          try { console.warn("onVadSilenceStop failed", err); } catch {}
        }
      }
    }, timeoutMs);
  }

  function clearVadSilenceTimer() {
    if (vadSilenceTimerId) {
      clearTimeout(vadSilenceTimerId);
      vadSilenceTimerId = null;
    }
  }

  function initClientVad(onGateChangeOverride = null) {
    try {
      vadController = initVAD({
        getPolicy: () => getClientVadPolicyConfig(),
        getTtsActive: () => AppState?.ttsActive,
        onGateChange: (state) => {
          if (typeof onGateChangeOverride === "function") {
            try { onGateChangeOverride(state); } catch {}
          }
          handleVadGateChange(state);
        },
        setAppState: (patch) => setVadAppState(patch),
        publish: (event, payload) => publishVad(event, payload),
      });
    } catch (err) {
      try {
        console.warn("VAD initialization failed", err);
      } catch {}
      vadController = null;
    }
  }

  // ===== Recorder lifecycle =====

  function setAppStateValue(key, value) {
    if (typeof key !== "string" || !key) {
      return;
    }
    const state = typeof AppState?.getState === "function" ? AppState.getState() : null;
    const hasKey = state && Object.prototype.hasOwnProperty.call(state, key);
    const current = hasKey ? state[key] : AppState?.[key];
    if (Object.is(current, value)) {
      if (AppState && typeof AppState === "object") {
        AppState[key] = value;
      }
      return;
    }
    if (AppState && typeof AppState === "object") {
      AppState[key] = value;
    }
    updateState({ [key]: value });
  }

  function setListeningState(active) {
    const nextListening = Boolean(active);
    if (nextListening) {
      if (!pcmSenderReady) {
        try {
          logStage("client.mic", {
            outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
            message: "setListeningState(true) called before pcmSenderReady",
          });
        } catch {}
        return;
      }
      if (!audioStreaming) {
        try {
          logStage("client.mic", {
            outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
            message: "setListeningState(true) called while audioStreaming=false",
          });
        } catch {}
        return;
      }
    }
    updateState({ listening: nextListening });
    if (!nextListening && AppState?.wsConnected) {
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
    firstChunkSeen = false;
    armingGraceUntil = 0;
  }

  function softPauseCapture(reason = "turn_completed") {
    const pauseReason = normalizeReason(reason);
    try {
      setSenderPauseReason(pauseReason, true);
      updatePcmSenderState();
    } catch {}
    try {
      hubLog("client.pcm.soft_pause", { reason: pauseReason });
    } catch {}
  }

  async function performStopRecorder(reason, options = {}) {
    const opts = options && typeof options === "object" ? options : {};
    const softStop = opts.softStop === true || opts.soft === true;

    audioStreaming = false;
    firstChunkSeen = false;
    armingGraceUntil = 0;
    const stopReason = normalizeReason(reason);
    if (typeof resetTurnIntent === "function") {
      try { resetTurnIntent(stopReason); } catch {}
    }
    if (softStop) {
      scheduleAudioKeepalive();
    } else {
      clearAudioKeepaliveTimer();
    }
    clearVadSilenceTimer();
    if (typeof clearPartialWatchdog === "function") {
      try { clearPartialWatchdog(); } catch {}
    }
    resetSilenceSuppression();
    syncSenderPaused(softStop);
    try {
      logMic({ outcome: "STOPPED", source: "capture_runtime" });
      logStage("client.mic.stopped", { source: "capture_runtime", reason: stopReason || "unknown", phase: resolvePhase() });
    } catch (_) {}
    try {
      const sender = await ensurePcmSender();
      if (sender && typeof sender.setEnabled === "function") {
        try {
          sender.setEnabled(false);
        } catch (err) {
          try { console.warn("pcm.sender.disable_failed", err); } catch {}
        }
      }
    } catch (err) {
      try { console.warn("pcm.sender.disable_failed", err); } catch {}
    }
    micRecordingStartAt = null;
    if (vadController && typeof vadController.reset === "function") {
      try {
        vadController.reset();
      } catch (err) {
        try { console.warn("VAD reset failed", err); } catch {}
      }
    }
    setListeningState(false);
    updatePcmSenderState();
    try {
      logStage("client.pcm.capture_stop", {
        source: "capture_runtime",
        reason: stopReason || "unknown",
        phase: resolvePhase(),
      });
    } catch (_) {}
    if (!softStop) {
      try {
        if (typeof onCaptureStop === "function") {
          onCaptureStop({
            reason: stopReason,
            source: opts.source || null,
            sender_paused: senderPaused,
            arming_grace_until: armingGraceUntil,
          });
        }
      } catch (err) {
        try { console.warn("onCaptureStop handler failed", err); } catch {}
      }
      try {
        hubLog("client.pcm.capture_stop", { reason: stopReason });
      } catch {}
    }
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
    const legacyFallback = arguments.length > 1 && typeof options !== "object"
      ? options
      : undefined;

    const opts =
      options && typeof options === "object" && !Array.isArray(options)
        ? options
        : {};

    const fallbackReason = Object.prototype.hasOwnProperty.call(opts, "fallbackReason")
      ? opts.fallbackReason
      : legacyFallback;

    const allowVadStop = opts.allowVadStop === true;
    const source = Object.prototype.hasOwnProperty.call(opts, "source")
      ? opts.source
      : null;

    const { allowed, blocked, label } = evaluateStopRecorderReason(reason, fallbackReason);

    if (!allowed && !allowVadStop) {
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

    const normalizedLabel =
      typeof label === "string" && label
        ? label
        : normalizeReason(fallbackReason || reason);

    return performStopRecorder(normalizedLabel, opts);
  }

  function streamIsActive(stream) {
    try {
      const tracks = typeof stream?.getAudioTracks === "function" ? stream.getAudioTracks() : [];
      return Array.isArray(tracks) && tracks.some((track) => track && track.readyState === "live");
    } catch (_) {
      return false;
    }
  }

  async function startCaptureFromPolicy(policy, reason = "auto") {
    if (captureStream && streamIsActive(captureStream)) {
      ensureStreamProvider(captureStream);
      return captureStream;
    }
    const constraints = buildConstraintsFromPolicy(policy || {});
    const summary = summarizeConstraints(constraints);
    if (typeof window !== "undefined") {
      try {
        window.__askchip_LastMicConstraints = constraints;
      } catch (_) {}
    }
    const gum = typeof window !== "undefined" && typeof window.getMicOnce === "function"
      ? window.getMicOnce
      : null;
    try {
      try {
        logStage("client.mic.request_gum", {
          source: "capture_runtime",
          reason,
          constraints: summary,
          phase: resolvePhase(),
        });
      } catch (_) {}
      try {
        logStage("client.mic.gum_attempt", {
          source: "capture_runtime",
          reason,
          constraints: summary,
          phase: resolvePhase(),
        });
      } catch (_) {}
      const stream = gum
        ? await gum(constraints)
        : await navigator.mediaDevices.getUserMedia(constraints);
      captureStream = stream;
      if (typeof window !== "undefined") {
        try {
          window.__askchip_MicStream = stream;
        } catch (_) {}
      }
      ensureStreamProvider(stream);
      try {
        const track = (stream && stream.getAudioTracks && stream.getAudioTracks()[0]) || null;
        const trackSettings = track && track.getSettings ? track.getSettings() : {};
        logStage("client.mic.stream_success", {
          source: "capture_runtime",
          reason,
          sampleRate: trackSettings.sampleRate || null,
          deviceId: trackSettings.deviceId || null,
          channelCount: trackSettings.channelCount || null,
          trackState: track?.readyState || null,
          phase: resolvePhase(),
        });
        logStage("client.mic.gum_success", {
          source: "capture_runtime",
          reason,
          track: track?.label || null,
          trackState: track?.readyState || null,
          constraints: summary,
          phase: resolvePhase(),
        });
      } catch (_) {}
      try {
        logMic({ outcome: "OPENED", source: "capture_runtime", constraints_summary: summary });
        logStage("client.mic.opened", { source: "capture_runtime", reason, constraints: summary, phase: resolvePhase() });
      } catch (_) {}
      if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        try {
          window.dispatchEvent(new CustomEvent("EVT_CLIENT_MIC_OPEN", {
            detail: { source: "capture_runtime", ts_ms: Date.now(), constraints: summary },
          }));
        } catch (_) {}
      }
      return captureStream;
    } catch (err) {
      try {
        logStage("client.mic.start_failed", { source: "capture_runtime", reason: err?.message || "gum_failed", phase: resolvePhase() });
      } catch (_) {}
      try {
        logStage("client.mic.start_failed_error", {
          source: "capture_runtime",
          reason: err?.message || "gum_failed",
          error_name: err?.name || null,
          error_detail: err?.message || null,
          phase: resolvePhase(),
        });
      } catch (_) {}
      // On a getUserMedia / mic acquisition failure, request a graceful
      // input.stop from the server side without killing the websocket.
      try {
        const maybeClient =
          typeof window !== "undefined"
            ? window.wsClient || window.WSClient || null
            : null;

        if (maybeClient && typeof maybeClient.send === "function") {
          try {
            logStage("client.mic.gum_failure_input_stop", {
              source: "capture_runtime",
              phase: resolvePhase(),
              hasClient: true,
            });
          } catch (_) {}

          // We deliberately allow this even if the client thinks it is not
          // yet in a "ready" phase; the server will decide how to react.
          maybeClient.send(
            { type: "input.stop", reason: "mic_gum_failure" },
            { skipPhaseCheck: true }
          );
        } else {
          try {
            logStage("client.mic.gum_failure_input_stop_skipped", {
              source: "capture_runtime",
              phase: resolvePhase(),
              hasClient: Boolean(maybeClient),
            });
          } catch (_) {}
        }
      } catch (_) {
        // Best-effort; do not rethrow
      }
      return null;
    }
  }

  async function startRecorderStreaming(opts = {}) {
    let policy = null;
    let reason = null;

    if (opts && typeof opts === "object" && !Array.isArray(opts)) {
      if ("policy" in opts || "reason" in opts) {
        // Called like startRecorderStreaming({ policy, reason })
        policy = opts.policy ?? null;
        reason = opts.reason ?? null;
      } else {
        // Called like startRecorderStreaming(policyObject)
        policy = opts;
        reason = null;
      }
    } else {
      // Primitive or other unexpected input; treat as policy
      policy = opts;
      reason = null;
    }

    const alreadyListening = Boolean(AppState?.listening);
    if (alreadyListening) {
      try {
        logStage("client.mic", {
          outcome: MIC_OUTCOME.ERROR_STATE_GUARD,
          message: "startRecorderStreaming called while AppState.listening=true",
        });
      } catch {}
    }

    firstChunkSeen = false;
    clearVadSilenceTimer();
    const captureReason = typeof reason === "string" && reason ? reason : "auto";
    try {
      const stream = await startCaptureFromPolicy(policy || {}, captureReason);
      if (!stream) {
        setListeningState(false);
        audioStreaming = false;
        return false;
      }
      const sender = await ensurePcmSender();
      if (!sender) {
        try {
          logStage("client.mic", {
            outcome: MIC_OUTCOME.ERROR_SENDER_INIT || MIC_OUTCOME.ERROR_UNKNOWN,
            name: "SenderInitError",
            message: "PCM sender unavailable",
          });
        } catch {}
        try {
          recordClientBannerEvent?.("mic.error", {
            name: "SenderInitError",
            detail: "PCM sender unavailable",
          });
        } catch {}
        setListeningState(false);
        audioStreaming = false;
        console.warn("PCM sender unavailable; cannot start streaming");
        return false;
      }
      if (!pcmSenderReady) {
        pcmSenderReady = true;
        if (!pcmSenderReadyLogged) {
          pcmSenderReadyLogged = true;
          try {
            logStage("client.mic", {
              outcome: MIC_OUTCOME.STREAMING_HEARTBEAT,
              message: "pcm_sender_ready",
            });
          } catch {}
        }
      }
      resetRecorderTelemetry();
      resetSilenceSuppression();
      syncSenderPaused(false);
      if (vadController && typeof vadController.reset === "function") {
        try {
          vadController.reset();
        } catch (err) {
          try { console.warn("VAD reset failed", err); } catch {}
        }
      }
      if (typeof sender.resume === "function") {
        await sender.resume();
      }
      micRecordingStartAt = Date.now();
      audioStreaming = true;
      updatePcmSenderState();
      scheduleAudioKeepalive();
      setListeningState(true);
      const normalizedCaptureReason = normalizeReason(captureReason);
      try {
        logStage("client.pcm.capture_start", {
          source: "capture_runtime",
          reason: normalizedCaptureReason || captureReason || "unknown",
          phase: resolvePhase(),
        });
      } catch (_) {}
      armingGraceUntil = Date.now() + 1200;
      try {
        hubLog("client.pcm.capture_start", { reason: captureReason, policy: !!policy });
      } catch {}
      return true;
    } catch (err) {
      const meta = {
        name: err?.name || "Error",
        message: err?.message || "",
      };
      const outcome = err?.name === "NotAllowedError"
        ? MIC_OUTCOME.ERROR_DENIED
        : MIC_OUTCOME.ERROR_UNKNOWN;
      try {
        logStage("client.mic", { outcome, ...meta });
      } catch {}
      try {
        recordClientBannerEvent?.("mic.error", {
          name: meta.name,
          detail: meta.message,
        });
      } catch {}
      setListeningState(false);
      audioStreaming = false;
      throw err;
    }
  }

  return {
    // VAD
    getVadController,
    setVadAppState,
    publishVad,
    handleVadGateChange,
    getClientVadPolicyConfig,
    getVadSilenceTimeoutMs,
    scheduleVadSilenceTimer,
    clearVadSilenceTimer,
    initClientVad,
    // Recorder
    evaluateStopRecorderReason,
    setAppStateValue,
    setListeningState,
    setAsrArmInFlight,
    setWsConnected,
    setWsPhase,
    resetRecorderTelemetry,
    softPauseCapture,
    performStopRecorder,
    stopRecorder,
    startRecorderStreaming,
  };
}
