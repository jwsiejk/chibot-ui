// app/static/js/audio/capture_runtime.js
// Encapsulates VAD+AppState bridge, consoleBus publishing, silence timers,
// and recorder start/stop lifecycle for AskChip.

export function createCaptureRuntime({
  AppState,
  policyRuntime,          // createPolicyRuntime(AppState)
  audioRuntime,           // createWsAudioRuntime(...)
  initVAD,                // existing initVAD(...) function
  consoleBus,             // existing console event bus
  hubLog,                 // logging helper
  logStage,               // telemetry logStage from ws/telemetry.js
  recordClientBannerEvent // telemetry banner from ws/telemetry.js
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

  const {
    getClientVadPolicyRoot = () => ({}),
  } = policyRuntime || {};

  const {
    getPcmRing = () => null,
    updatePcmSenderState = () => {},
  } = audioRuntime || {};

  const senderPauseReasons = new Set();
  let senderPaused = false;
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
    }, timeoutMs);
  }

  function clearVadSilenceTimer() {
    if (vadSilenceTimerId) {
      clearTimeout(vadSilenceTimerId);
      vadSilenceTimerId = null;
    }
  }

  function initClientVad() {
    try {
      vadController = initVAD({
        getPolicy: () => getClientVadPolicyConfig(),
        getTtsActive: () => AppState?.ttsActive,
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
  }

  // ===== Recorder lifecycle =====

  function evaluateStopRecorderReason(reason) {
    // real implementation will be moved from ws_client.js
    return "unknown";
  }

  function setAppStateValue(key, value) {
    // real implementation will be moved from ws_client.js
  }

  function setListeningState(listening) {
    // real implementation will be moved from ws_client.js
  }

  function setAsrArmInFlight(inFlight) {
    // real implementation will be moved from ws_client.js
  }

  function setWsConnected(connected) {
    // real implementation will be moved from ws_client.js
  }

  function setWsPhase(phase) {
    // real implementation will be moved from ws_client.js
  }

  function resetRecorderTelemetry() {
    // real implementation will be moved from ws_client.js
  }

  async function performStopRecorder(reason) {
    // real implementation will be moved from ws_client.js
  }

  async function stopRecorder(reason) {
    // real implementation will be moved from ws_client.js
  }

  async function startRecorderStreaming(opts = {}) {
    // real implementation will be moved from ws_client.js
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
    performStopRecorder,
    stopRecorder,
    startRecorderStreaming,
  };
}
