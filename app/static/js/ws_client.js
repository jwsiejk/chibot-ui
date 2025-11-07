// CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram; legacy Speechmatics removed.
import { initVAD } from "./audio/vad_client.js";
import { WakeWord } from "./wake_word.js";
(() => {
  const HEARTBEAT_INTERVAL_MS = 20000;
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const SUBPROTOCOL = "chat.v2";
  const INFO_DEADLINE_MS = 20000;
  const TOKEN_EXPIRY_MS = 60 * 1000;
  const TOAST_STYLE_ID = "wsclient-toast-styles";
  const TOAST_STYLE_TEXT = "#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:\"Inter\",system-ui,-apple-system,\"Segoe UI\",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}";
  const CLIENT_VAD_POLICY = Object.freeze({
    enable: true,
    sensitivity: 0.5,
    min_speech_ms: 200,
    min_silence_ms: 400,
    hold_ms: 250,
    echo_suppression_db: 10,
    tts_threshold_boost_db: 12,
    debounce_ms: 40,
    stream_gate: "none",
    max_gate_silence_ms: 3000,
  });
  const CLIENT_VAD_POLICY_ROOT = Object.freeze({ vad: Object.freeze({ client: CLIENT_VAD_POLICY }) });
  const VAD_SILENCE_TIMEOUT_SAMPLE_RATE = 10;

  const IGNORED_VENDOR_MESSAGES = new Set(["AddPartialTranscript", "AddTranscript"]);

  // ---- Golden-path turn trace & mic outcomes (additive) ----
  // ---- Telemetry (additive) ----
  const MIC_OUTCOME = {
    PERM_GRANTED: 'perm_granted',
    ARMED: 'armed',
    STREAMING: 'streaming',
    STREAMING_HEARTBEAT: 'streaming_heartbeat',
    STOPPED: 'stopped',
    ERROR_DENIED: 'error_denied',
    ERROR_NO_DEVICE: 'error_no_device',
    ERROR_GUM: 'error_getuser_media',
    ERROR_SILENT: 'error_silent_stream',
    ERROR_WS_SEND: 'error_ws_send',
    ERROR_STATE_GUARD: 'error_state_guard',
    ERROR_UNKNOWN: 'error_unknown',
  };
  const PCM_BREADCRUMB_POLICY = { input: 'pcm_16k', mode: 'pcm16' };
  const DEFAULT_ASR_VENDOR = 'speechmatics';

  let __micAttempts = 0;
  let __micChunks = 0;
  let __micBytes = 0;
  let __micArmedAt = 0;     // ms since epoch
  let __micPermissionGranted = false;
  let __micRecordingStartAt = null;
  let __micFirstChunkBreadcrumbSent = false;
  let __turnTraceId = null; // optional trace id per turn (sid + timestamp)
  let __pendingAsrReadyStart = null;
  let __autoVadPatchedForPendingStart = false;

  // Per-turn readiness/start tokens to prevent re-arm loops
  let __asrReadySeen = false;

  let _audioStreaming = false;

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
      Object.defineProperty(window, "__pendingAsrReadyStart", {
        configurable: true,
        get() { return __pendingAsrReadyStart; },
        set(value) {
          if (value === null || (value && typeof value === "object" && !Array.isArray(value))) {
            __pendingAsrReadyStart = value;
          }
        },
      });
    } catch {}
  }

  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading WSClient");
  }
  const P0 = AppState.policy || {};
  const P0Policy = P0 && typeof P0.policy === "object" && P0.policy
    ? { ...P0.policy }
    : {};
  AppState.policy = {
    ...P0,
    auto_record_after_greet: P0.auto_record_after_greet ?? true,
    require_user_gesture_first_visit: P0.require_user_gesture_first_visit ?? false,
    tts_gate_enabled: P0.tts_gate_enabled ?? true,
    autostart_retry_on: Array.isArray(P0.autostart_retry_on)
      ? P0.autostart_retry_on.slice()
      : ["asrReady", "ttsEnded", "turnState:Ready"],
    autostart_backoff_ms: Array.isArray(P0.autostart_backoff_ms)
      ? P0.autostart_backoff_ms.slice()
      : [0, 300, 1000],
    autostart_max_attempts: Number.isFinite(P0.autostart_max_attempts) ? P0.autostart_max_attempts : 5,
    show_tap_to_speak_cta_after_ms: Number.isFinite(P0.show_tap_to_speak_cta_after_ms)
      ? P0.show_tap_to_speak_cta_after_ms
      : 2000,
    reopen_asr_on_idle: P0.reopen_asr_on_idle ?? true,
    policy: {
      ...P0Policy,
      input: {
        ...(P0Policy && typeof P0Policy.input === "object" ? P0Policy.input : {}),
        require_hotword_to_start: false,
        require_user_gesture_first_visit: false,
      },
    },
  };

  function installClientVadPolicySnapshot() {
    const policyRoot = AppState.policy && typeof AppState.policy === "object"
      ? AppState.policy
      : (AppState.policy = {});
    const ensureVadBlock = (target) => {
      if (!target || typeof target !== "object") {
        return;
      }
      const existingVad = target.vad && typeof target.vad === "object" ? target.vad : {};
      target.vad = { ...existingVad, client: CLIENT_VAD_POLICY };
    };
    ensureVadBlock(policyRoot);
    if (!policyRoot.policy || typeof policyRoot.policy !== "object") {
      policyRoot.policy = {};
    }
    ensureVadBlock(policyRoot.policy);
  }

  installClientVadPolicySnapshot();

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

  function clonePolicySnapshot(source) {
    if (!source || typeof source !== "object") {
      return {};
    }
    return { ...source };
  }

  function patchPolicyVad(patch) {
    if (!patch || typeof patch !== "object") {
      return;
    }

    const applyPatch = (policyRoot) => {
      if (!policyRoot || typeof policyRoot !== "object") {
        return;
      }
      const currentVad = policyRoot.vad && typeof policyRoot.vad === "object"
        ? policyRoot.vad
        : {};
      policyRoot.vad = { ...currentVad, ...patch };
    };

    try {
      if (AppState && typeof AppState === "object") {
        const policyRoot = AppState.policy && typeof AppState.policy === "object"
          ? AppState.policy
          : (AppState.policy = {});
        applyPatch(policyRoot);
        if (policyRoot.policy && typeof policyRoot.policy === "object") {
          applyPatch(policyRoot.policy);
        }
      }
    } catch (err) {
      try {
        console.warn("Failed to update AppState policy VAD", err);
      } catch {}
    }

    const message = "client.mic diag=vad_active state=Listening";
    try {
      console.log(message);
    } catch {}
    try {
      hubLog("client.mic", { message });
    } catch (err) {
      try {
        console.warn("Failed to log VAD activation after asr.ready", err);
      } catch {}
    }
  }

  function setPendingAsrReadyStart(detail) {
    if (!detail || typeof detail !== "object") {
      __pendingAsrReadyStart = null;
      __autoVadPatchedForPendingStart = false;
      return;
    }
    const policy = clonePolicySnapshot(detail.policy);
    const frame = detail.frame && typeof detail.frame === "object" ? { ...detail.frame } : null;
    const reason = typeof detail.reason === "string" && detail.reason
      ? detail.reason
      : (typeof detail.source === "string" && detail.source ? detail.source : "start_listening");
    __pendingAsrReadyStart = {
      policy,
      frame,
      reason,
      ts: Date.now(),
    };
    __autoVadPatchedForPendingStart = false;
    try {
      console.info("diag=awaiting_asr_ready reason=%s", reason);
    } catch {}
  }

  function clearPendingAsrReadyStart(reason) {
    if (!__pendingAsrReadyStart) {
      return;
    }
    try {
      const label = typeof reason === "string" && reason ? reason : "unknown";
      console.info("diag=awaiting_asr_ready_clear reason=%s", label);
    } catch {}
    __pendingAsrReadyStart = null;
  }

  function markAutoVadActiveAfterAsrReady() {
    if (__autoVadPatchedForPendingStart) {
      return;
    }
    __autoVadPatchedForPendingStart = true;

    try {
      patchPolicyVad({ allow_auto_vad: true, auto_vad_active: true });
    } catch (err) {
      try {
        console.warn("Failed to patch policy VAD after asr.ready", err);
      } catch {}
    }
  }

  async function startStreamingAfterAsrReady(trigger) {
    const pending = __pendingAsrReadyStart;
    if (!pending) {
      return false;
    }
    __pendingAsrReadyStart = null;
    const reason = typeof trigger === "string" && trigger
      ? trigger
      : (typeof pending.reason === "string" && pending.reason ? pending.reason : "asr_ready");
    const policy = clonePolicySnapshot(pending.policy);
    try {
      if (!AppState.listening) {
        sendAudioHeader(policy);
      }
      await startRecorderStreaming(policy);
      logMic({ outcome: MIC_OUTCOME.STREAMING, reason });
      return true;
    } catch (err) {
      console.error("Deferred recorder start failed", err);
      logMic({
        outcome: MIC_OUTCOME.ERROR_GUM,
        message: err && err.message ? err.message : String(err || ""),
        reason,
      });
      __pendingAsrReadyStart = pending;
      return false;
    }
  }

  let userGestureSatisfied = !AppState.policy.require_user_gesture_first_visit;
  let autostartAttempts = 0;
  let autostartTimer = null;

  const WSClient = window.WSClient = window.WSClient || {};
  if (typeof window !== "undefined" && typeof window.ws === "undefined") {
    window.ws = null;
  }
  WSClient._ws = WSClient._ws || null;
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
  const getAudioPlayer = () => window.AudioPlayer;

  const VAD_APPSTATE_KEYS = [
    "vadActive",
    "vadSpeech",
    "vadConfidence",
    "vadEnergyDb",
    "vadNoiseDb",
  ];
  let vadController = null;
  let vadSilenceTimerId = null;
  let senderPaused = false;
  const PCM_TARGET_BATCH_MS = 60;
  const PCM_FLUSH_TIMER_MS = 50;
  const PCM_SAMPLE_RATE = 16000;
  const PCM_SAMPLES_PER_MS = PCM_SAMPLE_RATE / 1000;
  let pcmBatchQueue = [];
  let pcmBatchSampleCount = 0;
  let pcmFlushTimerId = null;

  function hubLog(label, detail) {
    const state = typeof window !== "undefined" ? window.AppState : null;
    const hub = state && state.hub;
    if (hub && typeof hub.log === "function") {
      try {
        hub.log(label, detail);
        return true;
      } catch (err) {
        console.warn("AppState.hub.log failed", err);
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

  function getVadPolicySnapshot() {
    return CLIENT_VAD_POLICY_ROOT;
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
    const policyRoot = AppState && typeof AppState.policy === "object" ? AppState.policy : null;
    if (policyRoot && typeof policyRoot === "object") {
      const direct = policyRoot.vad && typeof policyRoot.vad === "object" ? policyRoot.vad : null;
      if (direct && typeof direct.client === "object") {
        return direct.client;
      }
      const nestedRoot = policyRoot.policy && typeof policyRoot.policy === "object" ? policyRoot.policy : null;
      if (nestedRoot) {
        const nested = nestedRoot.vad && typeof nestedRoot.vad === "object" ? nestedRoot.vad : null;
        if (nested && typeof nested.client === "object") {
          return nested.client;
        }
      }
    }
    return CLIENT_VAD_POLICY;
  }

  function getVadSilenceTimeoutMs() {
    const config = getClientVadPolicyConfig();
    const candidate = config && Object.prototype.hasOwnProperty.call(config, "max_gate_silence_ms")
      ? Number(config.max_gate_silence_ms)
      : Number(config && config.max_silence_ms);
    if (Number.isFinite(candidate) && candidate > 0) {
      return Math.round(candidate);
    }
    const fallback = Number(CLIENT_VAD_POLICY.max_gate_silence_ms);
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
    } else if (event === "client.vad.speech_end") {
      const durationValue = Number(payload && payload.duration_ms);
      const durationMs = Number.isFinite(durationValue) ? Math.max(0, Math.round(durationValue)) : null;
      const detail = durationMs !== null ? { duration_ms: durationMs } : undefined;
      emitConsoleBusEvent("client.vad.end_speech", detail);
      scheduleVadSilenceTimer();
    }
    hubLog(event, payload);
  }

  function handleVadGateChange(action) {
    if (action === "pause") {
      senderPaused = true;
      return;
    }
    if (action === "resume") {
      senderPaused = false;
    }
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

  function emitMicBreadcrumb(detail = {}) {
    try {
      const payload = { ...detail };
      payload.gates = getGateSnapshot();
      hubLog('client.mic', payload);
    } catch (err) {
      try {
        console.warn("Mic breadcrumb log failed", err);
      } catch {}
    }
  }

  try {
    if (typeof AppState.websocket === "undefined" && typeof AppState.getState === "function") {
      Object.defineProperty(AppState, "websocket", {
        configurable: true, enumerable: false,
        get() { try { return AppState.getState().websocket || null; } catch { return null; } }
      });
    }
  } catch {}

  function logMic(detail = {}) {
    try {
      const holdFlags = {
        ttsActive: !!AppState?.ttsActive,
        systemHold: !!AppState?.systemHold,
        userMuted: !!AppState?.userMuted,
      };
      const base = {
        trace_id: __turnTraceId || null,
        attempts: __micAttempts,
        chunks: __micChunks,
        bytes: __micBytes,
        phase: AppState?.ttsActive ? 'tts_active' : 'post_tts',
        hold_flags: holdFlags,
      };
      const outcome = typeof detail?.outcome === "string" ? detail.outcome : null;
      const permLabel = typeof detail?.perm === "string" ? detail.perm : null;
      if (permLabel !== null) {
        const granted = permLabel === "granted";
        __micPermissionGranted = granted;
        try { AppState.micPermissionGranted = granted; } catch {}
      } else if (outcome === MIC_OUTCOME.ERROR_DENIED) {
        __micPermissionGranted = false;
        try { AppState.micPermissionGranted = false; } catch {}
      }
      if (outcome === MIC_OUTCOME.PERM_GRANTED || permLabel === "granted") {
        emitMicBreadcrumb({ event: "armed" });
      }
      if (outcome === MIC_OUTCOME.STREAMING && !__micFirstChunkBreadcrumbSent) {
        __micFirstChunkBreadcrumbSent = true;
        let msSinceStart = 0;
        if (typeof __micRecordingStartAt === "number") {
          msSinceStart = Math.max(0, Math.round(Date.now() - __micRecordingStartAt));
        } else if (Number.isFinite(Number(detail?.first_chunk_ms))) {
          const fallback = Number(detail.first_chunk_ms);
          msSinceStart = Math.max(0, Math.round(fallback));
        }
        const bytesRaw = Number.isFinite(__micBytes) ? __micBytes : 0;
        const bytesSent = bytesRaw >= 0 ? bytesRaw : 0;
        emitMicBreadcrumb({
          event: "first_chunk_sent",
          bytes: bytesSent,
          ms_since_recording_start: msSinceStart,
        });
      }
      if (outcome === MIC_OUTCOME.STOPPED) {
        let totalMs = 0;
        if (typeof __micRecordingStartAt === "number") {
          totalMs = Math.max(0, Math.round(Date.now() - __micRecordingStartAt));
        }
        const reason = typeof detail?.reason === "string" && detail.reason ? detail.reason : null;
        emitMicBreadcrumb({
          event: "stopped",
          reason,
          ms_total_recording: totalMs,
        });
        __micRecordingStartAt = null;
        __micFirstChunkBreadcrumbSent = false;
      }
      hubLog('client.mic', { ...base, ...detail });
    } catch {}
  }

  function logStage(label, detail = {}) {
    try {
      hubLog(label, { trace_id: __turnTraceId || null, ...detail });
    } catch {}
  }

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
  function __resetAudioHeaderSent() { __audioHeaderSent = false; }
  function __buildStrictAudioHeader(frameOrPolicy) {
    const base = AUDIO_HEADER_FRAME;
    const source = (frameOrPolicy?.policy || frameOrPolicy) || {};
    const audioPolicy = source.audio || {};
    const mediaAudioPolicy = (source.media && source.media.audio) || {};
    const formatCandidate = audioPolicy.format ?? mediaAudioPolicy.format ?? base.format;
    const rateCandidate =
      audioPolicy.sample_rate ??
      audioPolicy.rate ??
      mediaAudioPolicy.sample_rate ??
      mediaAudioPolicy.rate ??
      base.sample_rate;
    const channelsCandidate =
      audioPolicy.channels ??
      mediaAudioPolicy.channels ??
      base.channels;

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
    };
  }
  function __sendAudioHeaderOnce(frameOrPolicy) {
    if (__audioHeaderSent) { return; }
    try {
      const header = __buildStrictAudioHeader(frameOrPolicy);
      WSClient.send(header);
      logStage("client.audio_header_send", header);
      __audioHeaderSent = true;
    } catch (err) {
      console.warn("Failed to send audio header", err);
    }
  }
  function sendAudioHeader(frameOrPolicy) {
    __sendAudioHeaderOnce(frameOrPolicy);
  }
  // --- End: header idempotency + strict schema ---
  let __lastErrorSig = null, __lastErrorAt = 0;
  const AUDIO_KEEPALIVE_MS = 20000;
  const POST_TTS_ARM_DELAY_MS = 300;

  let socket = null;
  let heartbeatTimerId = null;
  let expectInfoFrame = true;
  let infoWatchdogTimerId = null;
  let lastPingAt = null;
  let transportFactory = (url, protocols = SUBPROTOCOL) => new WebSocket(url, protocols);
  let rateLimitRetryTimerId = null;
  let rateLimitRetryCount = 0;
  let autoResumeAttemptToken = null;
  let toastRoot = null;
  let lastTokenValue = null;
  let lastTokenMintedAt = null;

  let recorderInstance = null;
  let micKeepaliveTimerId = null;
  let micLastChunkAt = 0;
  let postTtsArmTimerId = null;

  function getRecorder() {
    if (recorderInstance && typeof recorderInstance.startListening === "function") {
      return recorderInstance;
    }
    const candidate = typeof window !== "undefined" ? window.AudioRecorder : null;
    if (!candidate || typeof candidate.startListening !== "function") {
      return null;
    }
    recorderInstance = candidate;
    return recorderInstance;
  }

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
    setAppStateValue("listening", listening);
    const recorderState = { active: listening };
    AppState.recorderActive = listening;
    updateState({ recorder: recorderState, recorderActive: listening });
    if (AppState.recorder && typeof AppState.recorder === "object") {
      AppState.recorder = { ...AppState.recorder, active: listening };
    } else {
      AppState.recorder = recorderState;
    }
    if (listening) {
      setWsPhase("streaming");
    } else if (AppState.wsConnected) {
      setWsPhase("connected");
    }
  }

  function setAsrArmInFlight(inFlight) {
    setAppStateValue("asrArmInFlight", Boolean(inFlight));
  }

  function setArmAfterTtsEnd(pending) {
    setAppStateValue("armAfterTtsEnd", Boolean(pending));
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
  }

  function recordRecorderChunk(timestampMs) {
    const now = Number.isFinite(timestampMs) ? timestampMs : Date.now();
    const currentCount = typeof AppState.chunkCount === "number"
      ? AppState.chunkCount
      : (typeof AppState.getState === "function" ? (AppState.getState().chunkCount || 0) : 0);
    const nextCount = currentCount + 1;
    AppState.chunkCount = nextCount;
    AppState.lastChunkTs = now;
    updateState({ chunkCount: nextCount, lastChunkTs: now });
  }

  function normalizeErrorDetail(detail) {
    if (detail === null || detail === undefined) {
      return null;
    }
    if (typeof detail === "string") {
      return truncateBannerString(detail, 240);
    }
    if (typeof detail === "number" || typeof detail === "boolean") {
      return truncateBannerString(String(detail), 240);
    }
    try {
      const serialized = JSON.stringify(detail);
      return truncateBannerString(serialized, 240);
    } catch (err) {
      try {
        return truncateBannerString(String(detail), 240);
      } catch {
        return null;
      }
    }
  }

  function recordLastError(code, detail) {
    const normalizedCode = Number.isFinite(code) ? code : null;
    const normalizedDetail = normalizeErrorDetail(detail);
    setAppStateValue("lastErrorCode", normalizedCode);
    setAppStateValue("lastErrorDetail", normalizedDetail);
  }

  function clearPostTtsArmTimer() {
    if (postTtsArmTimerId) {
      clearTimeout(postTtsArmTimerId);
      postTtsArmTimerId = null;
    }
  }

  function clearAudioKeepaliveTimer() {
    if (micKeepaliveTimerId) {
      clearTimeout(micKeepaliveTimerId);
      micKeepaliveTimerId = null;
    }
  }

  function scheduleAudioKeepalive() {
    clearAudioKeepaliveTimer();
    micKeepaliveTimerId = setTimeout(() => {
      micKeepaliveTimerId = null;
      const listening = Boolean(AppState.listening);
      const now = Date.now();
      if (!listening) {
        return;
      }
      if (now - micLastChunkAt >= AUDIO_KEEPALIVE_MS) {
        try {
          WSClient.send({ type: "client.ping" });
          logStage("client.ping", { lane: "mic" });
        } catch (err) {
          console.warn("client.ping send failed", err);
        }
      }
      scheduleAudioKeepalive();
    }, AUDIO_KEEPALIVE_MS);
  }

  function clearPcmFlushTimer() {
    if (pcmFlushTimerId) {
      clearTimeout(pcmFlushTimerId);
      pcmFlushTimerId = null;
    }
  }

  function resetPcmBatchState() {
    pcmBatchQueue = [];
    pcmBatchSampleCount = 0;
    clearPcmFlushTimer();
  }

  function schedulePcmFlushTimer() {
    if (!pcmFlushTimerId) {
      pcmFlushTimerId = setTimeout(() => {
        pcmFlushTimerId = null;
        flushPcmBatch();
      }, PCM_FLUSH_TIMER_MS);
    }
  }

  function enqueuePcmFrame(frame, meta) {
    if (!(frame instanceof Int16Array) || frame.length === 0) {
      return;
    }
    const metadata = meta && typeof meta === "object" ? { ...meta } : {};
    pcmBatchQueue.push({ frame, meta: metadata });
    pcmBatchSampleCount += frame.length;
    schedulePcmFlushTimer();
    const accumulatedMs = pcmBatchSampleCount / PCM_SAMPLES_PER_MS;
    if (accumulatedMs >= PCM_TARGET_BATCH_MS) {
      flushPcmBatch();
    }
  }

  function flushPcmBatch() {
    if (!pcmBatchQueue.length || pcmBatchSampleCount <= 0) {
      resetPcmBatchState();
      return;
    }
    const frames = pcmBatchQueue;
    const totalSamples = pcmBatchSampleCount;
    pcmBatchQueue = [];
    pcmBatchSampleCount = 0;
    clearPcmFlushTimer();
    if (!totalSamples) {
      return;
    }
    if (!_audioStreaming) {
      resetPcmBatchState();
      return;
    }

    const out = new Int16Array(totalSamples);
    let offset = 0;
    let firstSeq = 0;
    let batchChunks = 0;
    for (const entry of frames) {
      if (!batchChunks && entry && entry.meta && Number.isFinite(entry.meta.seq)) {
        firstSeq = Number(entry.meta.seq);
      }
      if (entry && entry.frame) {
        out.set(entry.frame, offset);
        offset += entry.frame.length;
        batchChunks += 1;
      }
    }
    if (!batchChunks) {
      return;
    }
    const bytes = out.byteLength;
    logStage("client.audio_chunk_send", { seq: firstSeq, bytes, batch_chunks: batchChunks });
    const ws = socket || (WSClient && WSClient._ws) || null;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("pcm.send.skipped", { readyState: ws ? ws.readyState : undefined });
      return;
    }
    try {
      ws.send(out.buffer);
      if ((Math.random() * 50 | 0) === 0) {
        const ms_est = Math.round(out.length / PCM_SAMPLES_PER_MS);
        hubLog("client.pcm.flush", { samples: out.length, ms_est, ws_state: ws.readyState });
      }
      __micChunks = (Number.isFinite(__micChunks) ? __micChunks : 0) + batchChunks;
      __micBytes = (Number.isFinite(__micBytes) ? __micBytes : 0) + bytes;
    } catch (err) {
      console.warn("pcm.send.error", err);
    }
  }

  function stopRecorder(reason) {
    _audioStreaming = false;
    clearAudioKeepaliveTimer();
    clearVadSilenceTimer();
    const recorder = getRecorder();
    if (!recorder) {
      setListeningState(false);
      return;
    }
    try {
      recorder.stopListening({ reason: normalizeReason(reason) });
    } catch (err) {
      console.warn("Recorder stopListening failed", err);
    }
    senderPaused = false;
    flushPcmBatch();
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
  }

  function handleRecorderChunk(event) {
    if (!event) {
      return;
    }
    const buffer = event.buffer instanceof ArrayBuffer
      ? event.buffer
      : (ArrayBuffer.isView(event.buffer)
        ? event.buffer.buffer.slice(event.buffer.byteOffset, event.buffer.byteOffset + event.buffer.byteLength)
        : null);
    if (!buffer) {
      return;
    }
    const seq = Number(event.seq) || 0;
    micLastChunkAt = Date.now();
    scheduleAudioKeepalive();
    recordRecorderChunk(micLastChunkAt);
    const frameTimestamp = typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
    if (vadController && typeof vadController.onPcmFrame === "function") {
      try {
        vadController.onPcmFrame(buffer, frameTimestamp);
      } catch (err) {
        try {
          console.warn("VAD frame processing failed", err);
        } catch {}
      }
    }
    let asrReady = false;
    try {
      if (typeof AppState.getState === "function") {
        const snapshot = AppState.getState();
        if (snapshot && typeof snapshot.asrReady === "boolean") {
          asrReady = snapshot.asrReady;
        }
      }
    } catch {}
    if (!asrReady) {
      asrReady = Boolean(AppState?.asrReady);
    }
    if (senderPaused || !asrReady) {
      return;
    }
    if (seq === 0) {
      logStage("client.audio_first_chunk", { bytes: buffer.byteLength });
    }
    const frame = new Int16Array(buffer);
    enqueuePcmFrame(frame, { seq, bytes: buffer.byteLength });
  }

  async function startRecorderStreaming(policy) {
    if (AppState.listening) {
      return;
    }
    resetPcmBatchState();
    clearVadSilenceTimer();
    const recorder = getRecorder();
    if (!recorder) {
      console.warn("AudioRecorder unavailable; cannot start streaming");
      return;
    }
    // The server owns the input.start lifecycle; never emit it from the client.
    try {
      await recorder.start({ onChunk: handleRecorderChunk, policy });
    } catch (err) {
      if (err && err.name === "NotAllowedError") {
        logStage("client.mic", { outcome: MIC_OUTCOME.ERROR_DENIED, message: err.message || "permission" });
      }
      throw err;
    }
    resetRecorderTelemetry();
    senderPaused = false;
    if (vadController && typeof vadController.reset === "function") {
      try {
        vadController.reset();
      } catch (err) {
        try {
          console.warn("VAD reset failed", err);
        } catch {}
      }
    }
    await recorder.startListening(policy);
    micLastChunkAt = Date.now();
    scheduleAudioKeepalive();
    setListeningState(true);
  }

  function schedulePostTtsArm(reason) {
    clearPostTtsArmTimer();
    postTtsArmTimerId = setTimeout(() => {
      postTtsArmTimerId = null;
      requestAsrArm(reason);
    }, POST_TTS_ARM_DELAY_MS);
  }

  function requestAsrArm(reason) {
    const label = normalizeReason(reason);
    clearPostTtsArmTimer();
    if (AppState.ttsActive) {
      setArmAfterTtsEnd(true);
      logStage("client.asr_arm_queued", { reason: label });
      return;
    }
    if (AppState.asrArmInFlight) {
      logStage("client.asr_rearm_blocked", { reason: label });
      return;
    }
    try {
      setAsrArmInFlight(true);
      logStage("client.asr_rearm_request", { reason: label });
      WSClient.send({ type: "asr.open" });
      setWsPhase("arming");
    } catch (err) {
      setAsrArmInFlight(false);
      setWsPhase(AppState.wsConnected ? "connected" : "disconnected");
      console.error("Failed to send asr.open", err);
      logStage("client.mic", { outcome: MIC_OUTCOME.ERROR_WS_SEND, message: err?.message });
    }
  }

  function requestAsrClose(reason = "client_stop") {
    const label = normalizeReason(reason);
    clearPostTtsArmTimer();
    setArmAfterTtsEnd(false);
    setAsrArmInFlight(false);
    try {
      WSClient.send({ type: "asr.close", reason: label });
      setWsPhase("closing");
      logStage("client.asr_close_request", { reason: label });
    } catch (err) {
      console.warn("Failed to send asr.close", err);
      setWsPhase(AppState.wsConnected ? "connected" : "disconnected");
    }
    stopRecorder(label);
  }

  const CLIENT_BANNER_TYPE = "client.banner";
  const CLIENT_BANNER_MAX_HISTORY = 24;
  const CLIENT_BANNER_MAX_QUEUE = 24;
  const CLIENT_BANNER_EVENT_LABEL_MAX = 64;
  const CLIENT_BANNER_STRING_MAX = 240;

  let clientBannerQueue = [];

  // Initialize the client banner state only after related constants are defined.
  ensureClientBannerState();

  let wakeWordIgnoredLogged = false;
  WakeWord.onHotword(() => {
    if (wakeWordIgnoredLogged) {
      return;
    }
    wakeWordIgnoredLogged = true;
    try {
      console.info("wake_word_detected_ignore=1 reason=vad_barge_in_default");
    } catch {}
  });

  const USER_GESTURE_EVENTS = ["pointerdown", "touchstart", "keydown"];
  const AUTOSTART_TRIGGERS_ALWAYS = new Set(["boot", "gesture"]);

  let gestureListenerCleanup = null;

  function createEventEmitter() {
    const registry = new Map();
    return {
      on(event, handler) {
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
          listeners.delete(handler);
        };
      },
      emit(event, detail) {
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
    };
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
    if (typeof snapshot.armAfterTtsEnd === "undefined") {
      patch.armAfterTtsEnd = false;
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
    if (typeof snapshot.recorder === "undefined") {
      patch.recorder = { active: false };
    }
    if (typeof snapshot.recorderActive === "undefined") {
      patch.recorderActive = Boolean(AppState.recorderActive);
    }
    if (typeof snapshot.chunkCount === "undefined") {
      patch.chunkCount = Number.isFinite(AppState.chunkCount) ? AppState.chunkCount : 0;
    }
    if (typeof snapshot.lastChunkTs === "undefined") {
      patch.lastChunkTs = Number.isFinite(AppState.lastChunkTs) ? AppState.lastChunkTs : null;
    }
    if (typeof snapshot.lastErrorCode === "undefined") {
      patch.lastErrorCode = Number.isFinite(AppState.lastErrorCode) ? AppState.lastErrorCode : null;
    }
    if (typeof snapshot.lastErrorDetail === "undefined") {
      patch.lastErrorDetail = AppState.lastErrorDetail ?? null;
    }
    if (Object.keys(patch).length) {
      updateState(patch);
    }
    AppState.asrReady = Boolean(snapshot.asrReady);
    AppState.asrVendor = typeof snapshot.asrVendor === 'string' && snapshot.asrVendor
      ? snapshot.asrVendor
      : null;
    AppState.ttsActive = Boolean(snapshot.ttsActive);
    AppState.asrArmInFlight = Boolean(snapshot.asrArmInFlight);
    AppState.armAfterTtsEnd = Boolean(snapshot.armAfterTtsEnd);
    AppState.listening = Boolean(snapshot.listening);
    AppState.wsConnected = Boolean(snapshot.wsConnected);
    AppState.wsPhase = typeof snapshot.wsPhase === "string" ? snapshot.wsPhase : "disconnected";
    AppState.turnState = typeof snapshot.turnState === "string" ? snapshot.turnState : null;
    AppState.recorder = snapshot.recorder && typeof snapshot.recorder === "object"
      ? { active: Boolean(snapshot.recorder.active) }
      : { active: false };
    AppState.recorderActive = Boolean(snapshot.recorderActive ?? snapshot?.recorder?.active);
    AppState.chunkCount = Number.isFinite(snapshot.chunkCount) ? snapshot.chunkCount : 0;
    AppState.lastChunkTs = Number.isFinite(snapshot.lastChunkTs) ? snapshot.lastChunkTs : null;
    AppState.lastErrorCode = Number.isFinite(snapshot.lastErrorCode) ? snapshot.lastErrorCode : null;
    AppState.lastErrorDetail = snapshot.lastErrorDetail ?? null;
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
      maybeAutoStart("gesture");
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
    sendAutostartTelemetry("gesture", { reason });
  }

  function requiresHotwordToStart(snapshot) {
    const inputPolicy = snapshot?.input ?? null;
    if (!inputPolicy || typeof inputPolicy !== "object") {
      return false;
    }
    if (typeof inputPolicy.require_hotword_to_start === "boolean") {
      return inputPolicy.require_hotword_to_start;
    }
    return false;
  }

  function canAutoRecord(state) {
    if (requiresHotwordToStart(state)) return false;
    if (!state?.policy?.auto_record_after_greet) return false;
    if (state.policy.tts_gate_enabled && state.ttsActive) return false;
    return state.asrReady === true && state.turnState === "Ready" && !state.recorder?.active;
  }

  function reasonFromState(state) {
    if (requiresHotwordToStart(state)) return "wake_word_only";
    if (!userGestureSatisfied && state?.policy?.require_user_gesture_first_visit) return "needs_user_gesture";
    if (!state?.policy?.auto_record_after_greet) return "policy_disabled";
    if (state.policy.tts_gate_enabled && state.ttsActive) return "tts_active";
    if (!state.asrReady) return "asr_not_ready";
    if (state.turnState !== "Ready") return "turn_not_ready";
    if (state.recorder?.active) return "already_active";
    return null;
  }

  function shouldAttemptForTrigger(trigger, policy) {
    if (!trigger) {
      return true;
    }
    if (AUTOSTART_TRIGGERS_ALWAYS.has(trigger)) {
      return true;
    }
    const retries = Array.isArray(policy?.autostart_retry_on) ? policy.autostart_retry_on : [];
    if (!retries.length) {
      return true;
    }
    return retries.includes(trigger);
  }

  function getAutostartSnapshot() {
    const state = typeof AppState.getState === "function" ? AppState.getState() : {};
    const policy = AppState.policy || state.policy || {};
    const inputPolicy = policy && typeof policy.input === "object" ? policy.input : null;
    const recorderState = state && state.recorder && typeof state.recorder === "object"
      ? { active: Boolean(state.recorder.active) }
      : (AppState.recorder && typeof AppState.recorder === "object"
        ? { active: Boolean(AppState.recorder.active) }
        : { active: false });
    const ttsActive = typeof state.ttsActive === "boolean" ? state.ttsActive : Boolean(AppState.ttsActive);
    const turnState = typeof state.turnState === "string"
      ? state.turnState
      : (typeof AppState.turnState === "string" ? AppState.turnState : null);
    const asrReady = typeof state.asrReady === "boolean" ? state.asrReady : Boolean(AppState.asrReady);
    return {
      ...state,
      policy,
      input: inputPolicy,
      recorder: recorderState,
      ttsActive,
      turnState,
      asrReady,
    };
  }

  function sanitizeAutostartMeta(meta) {
    if (!meta || typeof meta !== "object") {
      return undefined;
    }
    const cleaned = {};
    const keys = Object.keys(meta);
    for (let i = 0; i < keys.length && i < 8; i += 1) {
      const key = keys[i];
      if (!key) continue;
      const value = meta[key];
      if (value === undefined) continue;
      if (typeof value === "string") {
        cleaned[key.slice(0, 48)] = truncateBannerString(value, 120);
      } else if (typeof value === "number") {
        if (Number.isFinite(value)) {
          cleaned[key.slice(0, 48)] = value;
        }
      } else if (typeof value === "boolean") {
        cleaned[key.slice(0, 48)] = value;
      }
    }
    return Object.keys(cleaned).length ? cleaned : undefined;
  }

  function sendAutostartTelemetry(event, meta) {
    if (typeof event !== "string" || !event) {
      return;
    }
    const payload = { type: "client.autostart", event };
    const sanitizedMeta = sanitizeAutostartMeta(meta);
    if (sanitizedMeta) {
      payload.meta = sanitizedMeta;
    }
    try {
      sendJson(payload);
    } catch (err) {
      console.warn("Failed to send autostart telemetry", err);
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

  function maybeAutoStart(trigger) {
    const snapshot = getAutostartSnapshot();
    const policy = snapshot.policy || {};
    const reason = reasonFromState(snapshot);
    if (reason) {
      sendAutostartTelemetry("blocked", { trigger, reason });
      return false;
    }
    if (!shouldAttemptForTrigger(trigger, policy)) {
      return false;
    }
    if (!canAutoRecord(snapshot)) {
      return false;
    }
    const maxAttempts = Number.isFinite(policy.autostart_max_attempts) ? policy.autostart_max_attempts : 5;
    if (autostartAttempts >= Math.max(0, maxAttempts)) {
      sendAutostartTelemetry("max_attempts", { trigger });
      return false;
    }
    const delays = Array.isArray(policy.autostart_backoff_ms) && policy.autostart_backoff_ms.length
      ? policy.autostart_backoff_ms
      : [0];
    const delayIndex = Math.min(autostartAttempts, delays.length - 1);
    const delay = Number(delays[delayIndex]) || 0;
    const execute = () => {
      autostartTimer = null;
      autostartAttempts += 1;
      sendAutostartTelemetry("attempt", { trigger, attempt: autostartAttempts });
      let result;
      try {
        result = invokeStartRecording(trigger || "auto");
      } catch (err) {
        console.warn("Auto startRecording failed", err);
        sendAutostartTelemetry("error", { trigger, message: getErrorMessage(err) });
        return;
      }
      const onFulfilled = (value) => {
        if (!value) {
          sendAutostartTelemetry("rejected", { trigger });
          return;
        }
        sendAutostartTelemetry("armed", { trigger });
      };
      const onRejected = (err) => {
        console.warn("Auto startRecording promise rejected", err);
        sendAutostartTelemetry("error", { trigger, message: getErrorMessage(err) });
      };
      if (result && typeof result.then === "function") {
        result.then(onFulfilled, onRejected);
      } else {
        onFulfilled(result);
      }
    };
    if (autostartTimer) {
      clearTimeout(autostartTimer);
      autostartTimer = null;
    }
    if (delay > 0) {
      autostartTimer = setTimeout(execute, delay);
    } else {
      execute();
    }
    return true;
  }

  function installAutostartRechecks() {
    if (!AppState) {
      return;
    }
    if (typeof AppState.subscribe === "function") {
      let previous = typeof AppState.getState === "function" ? AppState.getState() : {};
      AppState.subscribe((next) => {
        const events = [];
        const prevAsr = Boolean(previous.asrReady);
        const nextAsr = Boolean(next.asrReady);
        if (nextAsr && nextAsr !== prevAsr) {
          events.push("asrReady");
        }
        if (!nextAsr && prevAsr) {
          autostartAttempts = 0;
        }
        const prevTts = Boolean(previous.ttsActive);
        const nextTts = Boolean(next.ttsActive);
        if (nextTts !== prevTts) {
          events.push(nextTts ? "ttsActive" : "ttsEnded");
        }
        const prevTurn = typeof previous.turnState === "string" ? previous.turnState : null;
        const nextTurn = typeof next.turnState === "string" ? next.turnState : null;
        if (nextTurn !== prevTurn) {
          if (nextTurn === "Ready") {
            autostartAttempts = 0;
            events.push("turnState:Ready");
          } else if (prevTurn === "Ready") {
            autostartAttempts = 0;
          }
        }
        previous = {
          ...previous,
          asrReady: next.asrReady,
          ttsActive: next.ttsActive,
          turnState: next.turnState,
          recorder: next.recorder,
        };
        events.forEach((eventName) => maybeAutoStart(eventName));
      });
    } else {
      const watch = (keys, fn) => {
        let prevSnapshot = {};
        setInterval(() => {
          const snapshot = getAutostartSnapshot();
          let changed = false;
          const changedKeys = [];
          keys.forEach((key) => {
            if (prevSnapshot[key] !== snapshot[key]) {
              changed = true;
              changedKeys.push(key);
            }
          });
          if (changed) {
            prevSnapshot = {
              asrReady: snapshot.asrReady,
              ttsActive: snapshot.ttsActive,
              turnState: snapshot.turnState,
            };
            fn(changedKeys, snapshot);
          }
        }, 100);
      };
      watch(["asrReady", "ttsActive", "turnState"], (changedKeys, snapshot) => {
        changedKeys.forEach((key) => {
          if (key === "asrReady" && snapshot.asrReady) {
            maybeAutoStart("asrReady");
          } else if (key === "ttsActive" && !snapshot.ttsActive) {
            maybeAutoStart("ttsEnded");
          } else if (key === "turnState" && snapshot.turnState === "Ready") {
            autostartAttempts = 0;
            maybeAutoStart("turnState:Ready");
          }
        });
      });
    }
    AppState.on?.("turnReset", () => {
      autostartAttempts = 0;
      __turnTraceId = null;
      __asrReadySeen = false;
    });

    maybeAutoStart("boot");
  }

  function handleTurnStateEvent(event) {
    const detail = event && typeof event === "object" ? event.detail : null;
    const stateValue = detail && typeof detail === "object" && typeof detail.state === "string"
      ? detail.state
      : (detail && detail.meta && typeof detail.meta.state === "string" ? detail.meta.state : null);
    const normalized = typeof stateValue === "string" ? stateValue : null;
    const reasonValue = detail && typeof detail === "object" && typeof detail.reason === "string"
      ? detail.reason
      : (detail && detail.meta && typeof detail.meta.reason === "string" ? detail.meta.reason : null);
    AppState.turnState = normalized;
    updateState({ turnState: normalized });
    if (normalized === "Ready") {
      autostartAttempts = 0;
      __asrReadySeen = false;
      if (typeof AppState.emit === "function") {
        AppState.emit("turnReset", { state: normalized, reason: reasonValue || null });
      }
    }
  }

  ensureInitialAutostartState();
  attachUserGestureListeners();
  if (typeof window !== "undefined") {
    try {
      if (!window.__wsClientTurnStateListenerInstalled) {
        window.addEventListener("turn.state", handleTurnStateEvent);
        window.__wsClientTurnStateListenerInstalled = true;
      }
    } catch (err) {
      console.warn("Failed to bind turn.state listener", err);
    }
  }
  installAutostartRechecks();

  function truncateBannerString(value, max) {
    const limit = typeof max === "number" ? max : CLIENT_BANNER_STRING_MAX;
    if (typeof value !== "string") {
      return undefined;
    }
    if (!limit || limit <= 0) {
      return "";
    }
    if (value.length <= limit) {
      return value;
    }
    return `${value.slice(0, limit - 1)}…`;
  }

  function sanitizeBannerNumber(value) {
    if (typeof value !== "number") {
      return undefined;
    }
    if (!Number.isFinite(value)) {
      return undefined;
    }
    return value;
  }

  function sanitizeBannerArray(array, depth) {
    if (!Array.isArray(array)) {
      return undefined;
    }
    const limit = 8;
    const result = [];
    for (let i = 0; i < array.length && result.length < limit; i += 1) {
      const sanitized = sanitizeBannerValue(array[i], depth + 1);
      if (sanitized !== undefined) {
        result.push(sanitized);
      }
    }
    return result;
  }

  function sanitizeBannerObject(obj, depth) {
    if (!obj || typeof obj !== "object") {
      return undefined;
    }
    const result = {};
    const keys = Object.keys(obj);
    const limit = 16;
    for (let i = 0; i < keys.length && Object.keys(result).length < limit; i += 1) {
      const rawKey = keys[i];
      if (typeof rawKey !== "string" || !rawKey) {
        continue;
      }
      const key = truncateBannerString(rawKey, 48);
      const sanitized = sanitizeBannerValue(obj[rawKey], depth + 1);
      if (sanitized !== undefined) {
        result[key] = sanitized;
      }
    }
    return result;
  }

  function sanitizeBannerValue(value, depth = 0) {
    if (value === null) {
      return null;
    }
    if (typeof value === "string") {
      return truncateBannerString(value);
    }
    if (typeof value === "number") {
      return sanitizeBannerNumber(value);
    }
    if (typeof value === "boolean") {
      return value;
    }
    if (depth >= 2) {
      return undefined;
    }
    if (Array.isArray(value)) {
      return sanitizeBannerArray(value, depth);
    }
    if (typeof value === "object") {
      return sanitizeBannerObject(value, depth);
    }
    return undefined;
  }

  function sanitizeUrlForBanner(url) {
    if (typeof url !== "string" || !url) {
      return undefined;
    }
    try {
      const parsed = new URL(url, window.location && window.location.origin ? window.location.origin : undefined);
      const info = {
        origin: truncateBannerString(parsed.origin, 120),
        pathname: truncateBannerString(parsed.pathname, 160),
      };
      if (parsed.hash) {
        info.hash = truncateBannerString(parsed.hash, 80);
      }
      if (parsed.search) {
        const params = new URLSearchParams(parsed.search);
        const keys = [];
        const iterator = params.keys();
        for (let i = 0; i < 12; i += 1) {
          const { value, done } = iterator.next();
          if (done) {
            break;
          }
          if (typeof value === "string" && value) {
            keys.push(truncateBannerString(value, 64));
          }
        }
        if (keys.length) {
          info.query_keys = keys;
        }
      }
      return info;
    } catch (err) {
      return { raw: truncateBannerString(url) };
    }
  }

  function collectClientBannerInfo() {
    if (typeof window === "undefined") {
      return {};
    }
    const info = {};
    const nav = typeof navigator !== "undefined" ? navigator : null;
    if (nav) {
      if (typeof nav.userAgent === "string" && nav.userAgent) {
        info.user_agent = truncateBannerString(nav.userAgent);
      }
      if (typeof nav.platform === "string" && nav.platform) {
        info.platform = truncateBannerString(nav.platform, 120);
      }
      if (typeof nav.vendor === "string" && nav.vendor) {
        info.vendor = truncateBannerString(nav.vendor, 120);
      }
      if (typeof nav.product === "string" && nav.product) {
        info.product = truncateBannerString(nav.product, 120);
      }
      if (typeof nav.language === "string" && nav.language) {
        info.language = truncateBannerString(nav.language, 32);
      }
      if (Array.isArray(nav.languages) && nav.languages.length) {
        info.languages = nav.languages
          .filter((lang) => typeof lang === "string" && lang)
          .slice(0, 8)
          .map((lang) => truncateBannerString(lang, 32));
      }
      if (typeof nav.hardwareConcurrency === "number" && Number.isFinite(nav.hardwareConcurrency)) {
        info.hardware_concurrency = Math.max(0, Math.floor(nav.hardwareConcurrency));
      }
      if (typeof nav.deviceMemory === "number" && Number.isFinite(nav.deviceMemory)) {
        info.device_memory = Math.max(0, nav.deviceMemory);
      }
      if (typeof nav.maxTouchPoints === "number" && Number.isFinite(nav.maxTouchPoints)) {
        info.max_touch_points = Math.max(0, nav.maxTouchPoints);
      }
      if (typeof nav.cookieEnabled === "boolean") {
        info.cookies_enabled = nav.cookieEnabled;
      }
      if (typeof nav.onLine === "boolean") {
        info.online = nav.onLine;
      }
      const connection = nav.connection || nav.mozConnection || nav.webkitConnection;
      if (connection && typeof connection === "object") {
        const conn = {};
        if (typeof connection.effectiveType === "string" && connection.effectiveType) {
          conn.effective_type = truncateBannerString(connection.effectiveType, 32);
        }
        if (typeof connection.type === "string" && connection.type) {
          conn.type = truncateBannerString(connection.type, 32);
        }
        if (typeof connection.downlink === "number" && Number.isFinite(connection.downlink)) {
          conn.downlink = connection.downlink;
        }
        if (typeof connection.rtt === "number" && Number.isFinite(connection.rtt)) {
          conn.rtt = connection.rtt;
        }
        if (Object.keys(conn).length) {
          info.connection = conn;
        }
      }
    }
    const screenInfo = typeof window.screen !== "undefined" ? window.screen : null;
    if (screenInfo) {
      const screenPayload = {};
      if (Number.isFinite(screenInfo.width) && Number.isFinite(screenInfo.height)) {
        screenPayload.width = screenInfo.width;
        screenPayload.height = screenInfo.height;
      }
      if (Number.isFinite(screenInfo.availWidth) && Number.isFinite(screenInfo.availHeight)) {
        screenPayload.avail_width = screenInfo.availWidth;
        screenPayload.avail_height = screenInfo.availHeight;
      }
      if (Object.keys(screenPayload).length) {
        info.screen = screenPayload;
      }
    }
    if (Number.isFinite(window.innerWidth) && Number.isFinite(window.innerHeight)) {
      info.viewport = {
        width: window.innerWidth,
        height: window.innerHeight,
      };
    }
    if (typeof window.devicePixelRatio === "number" && Number.isFinite(window.devicePixelRatio)) {
      info.device_pixel_ratio = window.devicePixelRatio;
    }
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions();
      if (tz && typeof tz.timeZone === "string" && tz.timeZone) {
        info.timezone = truncateBannerString(tz.timeZone, 96);
      }
    } catch (err) {
      // ignore timezone errors
    }
    const offset = new Date().getTimezoneOffset();
    if (Number.isFinite(offset)) {
      info.tz_offset_minutes = offset;
    }
    if (typeof document !== "undefined") {
      if (typeof document.referrer === "string" && document.referrer) {
        info.referrer = truncateBannerString(document.referrer);
      }
      if (typeof document.visibilityState === "string" && document.visibilityState) {
        info.visibility_state = truncateBannerString(document.visibilityState, 32);
      }
    }
    if (typeof window.matchMedia === "function") {
      try {
        const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
        if (prefersDark && typeof prefersDark.matches === "boolean") {
          info.prefers_dark = prefersDark.matches;
        }
      } catch (err) {
        // ignore matchMedia errors
      }
    }
    if (typeof window.location !== "undefined") {
      const locationInfo = sanitizeUrlForBanner(window.location.href);
      if (locationInfo) {
        info.location = locationInfo;
      }
    }
    return info;
  }

  function ensureClientBannerState() {
    const state = AppState.getState();
    const existing = state && state.clientBanner && typeof state.clientBanner === "object" ? state.clientBanner : null;
    if (existing && existing.info) {
    return {
        info: existing.info,
        events: Array.isArray(existing.events) ? existing.events : [],
      };
    }
    const info = collectClientBannerInfo();
    const snapshot = { info, events: [] };
    updateState({ clientBanner: snapshot });
    return snapshot;
  }

  function updateClientBannerState(info, events) {
    const state = {
      info,
      events,
    };
    updateState({ clientBanner: state });
    return state;
  }

  function queueClientBannerPayload(payload) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    clientBannerQueue = clientBannerQueue.concat([payload]);
    if (clientBannerQueue.length > CLIENT_BANNER_MAX_QUEUE) {
      clientBannerQueue = clientBannerQueue.slice(clientBannerQueue.length - CLIENT_BANNER_MAX_QUEUE);
    }
    flushClientBannerQueue();
  }

  function flushClientBannerQueue() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    while (clientBannerQueue.length) {
      const next = clientBannerQueue[0];
      try {
        sendJson(next);
        clientBannerQueue.shift();
      } catch (err) {
        console.warn("Failed to flush client banner", err);
        break;
      }
    }
  }

  function recordClientBannerEvent(label, meta) {
    if (typeof label !== "string" || !label) {
      return;
    }
    const baseState = ensureClientBannerState();
    const info = collectClientBannerInfo();
    const events = Array.isArray(baseState.events) ? baseState.events.slice() : [];
    const entry = {
      label: truncateBannerString(label, CLIENT_BANNER_EVENT_LABEL_MAX),
      ts_ms: Date.now(),
    };
    const sanitizedMeta = sanitizeBannerValue(meta);
    if (sanitizedMeta && typeof sanitizedMeta === "object" && Object.keys(sanitizedMeta).length) {
      entry.meta = sanitizedMeta;
    }
    events.push(entry);
    if (events.length > CLIENT_BANNER_MAX_HISTORY) {
      events.splice(0, events.length - CLIENT_BANNER_MAX_HISTORY);
    }
    updateClientBannerState(info, events);
    queueClientBannerPayload({
      type: CLIENT_BANNER_TYPE,
      info,
      event: entry,
    });
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

  function updateState(patch) {
    AppState.setState(patch);
  }

  function ensureToastRoot() {
    toastRoot = toastRoot && toastRoot.isConnected ? toastRoot : document.getElementById("toast-root");
    if (!toastRoot) {
      toastRoot = document.createElement("div");
      toastRoot.id = "toast-root";
      toastRoot.className = "toast-container";
      document.body.appendChild(toastRoot);
    }
    if (
      !document.getElementById("inline-toast-styles") &&
      !document.getElementById("ws-error-styles") &&
      !document.getElementById(TOAST_STYLE_ID)
    ) {
      const styleTag = document.createElement("style");
      styleTag.id = TOAST_STYLE_ID;
      styleTag.textContent = TOAST_STYLE_TEXT;
      document.head.appendChild(styleTag);
    }
    return toastRoot;
  }

  function showConnectionToast(message) {
    if (!message) return;
    const host = ensureToastRoot();
    if (!host) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "alert");
    const body = document.createElement("div");
    body.className = "toast-body";
    body.textContent = message;
    toast.appendChild(body);
    host.appendChild(toast);
    setTimeout(() => {
      toast.classList.add("toast-exit");
      setTimeout(() => {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 220);
    }, 3600);
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
      const routing = AppState?.policy?.policy?.routing;
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

  function clearHeartbeat() {
    if (heartbeatTimerId) {
      clearInterval(heartbeatTimerId);
      heartbeatTimerId = null;
    }
    if (window.WSClient) {
      window.WSClient._hb = null;
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
      const state = AppState.getState();
      const resumeState = state && typeof state.resume === "object" ? state.resume : null;
      let resumeToken = null;
      if (resumeState && typeof resumeState.token === "string" && Number.isFinite(resumeState.expiresAt) && Date.now() < resumeState.expiresAt) {
        resumeToken = resumeState.token;
      }
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

  function sendBinary(payload, opts = {}) {
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (options.lane === "mic") {
      options.dropIfBusy = false;
    }
    const dropIfBusy = Boolean(options.dropIfBusy);
    const client = WSClient;
    const state = typeof AppState !== "undefined" && typeof AppState.getState === "function"
      ? AppState.getState()
      : null;
    const live = client && client._ws
      ? client._ws
      : (state && state.websocket ? state.websocket : null);
    if (dropIfBusy && live && live.readyState === WebSocket.OPEN && live.bufferedAmount > 512 * 1024) {
      return false;
    }
    if (client && typeof client.send === "function") {
      const result = client.send(payload, { binary: true });
      if (result && typeof result.then === "function") {
        return result;
      }
      return true;
    }
    try {
      if (live && live.readyState === WebSocket.OPEN) {
        live.send(payload);
        return true;
      }
    } catch (err) {
      console.error("WSClient sendBinary error", err);
    }
    return false;
  }

  function sendJson(frame) {
    try {
      send(frame);
    } catch (err) {
      console.error("WSClient sendJson error", err);
    }
  }

  function canSendHeartbeat() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    const client = WSClient;
    if (client && typeof client.isConnected === "function") {
      try {
        if (!client.isConnected()) {
          return false;
        }
      } catch (err) {
        console.warn("WSClient.isConnected check failed", err);
      }
    }
    return true;
  }

  function sendPing() {
    if (!canSendHeartbeat()) return;
    lastPingAt = Date.now();
    updateState({ lastPingAt });
    send({ type: "client.ping", ts: lastPingAt });
  }

  function startHeartbeat() {
    clearHeartbeat();
    const intervalId = setInterval(sendPing, HEARTBEAT_INTERVAL_MS);
    heartbeatTimerId = intervalId;
    if (window.WSClient) {
      window.WSClient._hb = intervalId;
    }
    sendPing();
    updateState({ heartbeatTimerId: intervalId });
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

  function startInputCapture(frame) {
    const policy = frame?.policy || {};
    const hasPolicy = policy && typeof policy === "object" && Object.keys(policy).length > 0;
    const source = frame?.type || "input.start";
    const unifiedRecorder = window?.AudioRecorder && typeof window.AudioRecorder.startListening === "function";

    if (unifiedRecorder) {
      try {
        logStage("client.input.capture", { source, hasPolicy, skipped: "unified_recorder" });
      } catch {}
      // In unified mode we don't start here; startStreamingAfterAsrReady() will.
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
    clearPendingAsrReadyStart(
      options && typeof options.reason === "string" ? options.reason : "stop_input_capture"
    );
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
    const gates = typeof getGateSnapshot === "function" ? getGateSnapshot() : null;
    const asrReady =
      typeof gates?.asrReady === "boolean" ? gates.asrReady : !!AppState?.asrReady;

    // Always stage a pending start so the asr.ready path can pick it up if it races.
    setPendingAsrReadyStart({
      frame,
      policy: frame?.policy || {},
      reason: frame?.reason || frame?.type || "input.start",
    });

    // If we're already ready, immediately run the same gate used by the asr.ready handler.
    if (asrReady) {
      try {
        await startStreamingAfterAsrReady("input.start_asr_ready");
      } catch (err) {
        console.error("input.start deferred start failed", err);
      }
    }

    // Do NOT call startInputCapture() here in unified mode;
    // startStreamingAfterAsrReady() will invoke AudioRecorder/hub correctly.
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
    AppState.asrReady = true;
    AppState.asrVendor = sanitized.vendor || DEFAULT_ASR_VENDOR;
    updateState({ asrReady: true, asrVendor: AppState.asrVendor });
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

  const ASR_VENDOR_OPTIONS = ['speechmatics'];
  const AUDIO_PIPELINE_OPTIONS = ['pcm16'];

  const DEFAULT_POLICY_FLAGS = {
    recorder: { stop_on_tts_start: false, mute_send_during_tts: true },
    input: { require_hotword_to_start: false, require_user_gesture_first_visit: false },
    asr: {
      prearm_on_tts_end: true,
      keep_stream_warm_ms: 30000,
      commit_on_vad_silence: true,
      commit_silence_ms: 900,
      max_utterance_ms: 8000,
      vendor: { primary: 'speechmatics', secondary: null },
    },
    routing: { ws_version: 'v2' },
    audio: { pipeline: { mode: 'pcm16' } },
  };

  function sanitizePolicySnapshot(source) {
    const base = (AppState && typeof AppState.policy === 'object') ? AppState.policy : {};
    const policy = { ...base };

    if (source && typeof source === 'object') {
      if (typeof source.mode === 'string') {
        policy.mode = source.mode;
      }
      if (typeof source.allow_auto_vad === 'boolean') {
        policy.allow_auto_vad = source.allow_auto_vad;
      }
      if (typeof source.barge_in_enabled === 'boolean') {
        policy.barge_in_enabled = source.barge_in_enabled;
      }
      if (typeof source.ws_auth_mode === 'string' && source.ws_auth_mode.trim()) {
        policy.ws_auth_mode = source.ws_auth_mode.trim();
      }
      if (typeof source.require_user_gesture_first_visit === 'boolean') {
        policy.require_user_gesture_first_visit = source.require_user_gesture_first_visit;
      }
      if (typeof source.auto_record_after_greet === 'boolean') {
        policy.auto_record_after_greet = source.auto_record_after_greet;
      }
      if (typeof source.tts_gate_enabled === 'boolean') {
        policy.tts_gate_enabled = source.tts_gate_enabled;
      }
      if (Array.isArray(source.autostart_retry_on)) {
        policy.autostart_retry_on = source.autostart_retry_on
          .filter((item) => typeof item === 'string' && item)
          .map((item) => item.slice(0, 64));
      }
      if (Array.isArray(source.autostart_backoff_ms)) {
        policy.autostart_backoff_ms = source.autostart_backoff_ms
          .map((item) => Number(item))
          .filter((value) => Number.isFinite(value) && value >= 0);
      }
      if (Number.isFinite(Number(source.autostart_max_attempts))) {
        policy.autostart_max_attempts = Number(source.autostart_max_attempts);
      }
      if (source.capture && typeof source.capture === 'object') {
        policy.capture = { ...source.capture };
      }
      if (source.media && typeof source.media === 'object') {
        policy.media = { ...source.media };
      }
      if (source.voice && typeof source.voice === 'object') {
        policy.voice = { ...source.voice };
      }
      if (source.greet && typeof source.greet === 'object') {
        policy.greet = { ...source.greet };
      }
      if (source.suggestions && typeof source.suggestions === 'object') {
        policy.suggestions = { ...source.suggestions };
      }
      if (source.actions && typeof source.actions === 'object') {
        policy.actions = { ...source.actions };
      }
      if (source.telemetry && typeof source.telemetry === 'object') {
        policy.telemetry = { ...source.telemetry };
      }
    }

    const existingNested = (policy && typeof policy.policy === 'object') ? policy.policy : {};
    const nested = {
      recorder: {
        ...DEFAULT_POLICY_FLAGS.recorder,
        ...(existingNested && typeof existingNested.recorder === 'object' ? existingNested.recorder : {}),
      },
      input: {
        ...DEFAULT_POLICY_FLAGS.input,
        ...(existingNested && typeof existingNested.input === 'object' ? existingNested.input : {}),
      },
      asr: {
        ...DEFAULT_POLICY_FLAGS.asr,
        ...(existingNested && typeof existingNested.asr === 'object' ? existingNested.asr : {}),
      },
      routing: {
        ...DEFAULT_POLICY_FLAGS.routing,
        ...(existingNested && typeof existingNested.routing === 'object' ? existingNested.routing : {}),
      },
    };

    const rawNested = source && typeof source === 'object' ? source.policy : null;
    if (rawNested && typeof rawNested === 'object') {
      const recorder = rawNested.recorder && typeof rawNested.recorder === 'object'
        ? rawNested.recorder
        : null;
      nested.recorder = {
        stop_on_tts_start: recorder && typeof recorder.stop_on_tts_start === 'boolean'
          ? recorder.stop_on_tts_start
          : DEFAULT_POLICY_FLAGS.recorder.stop_on_tts_start,
        mute_send_during_tts: recorder && typeof recorder.mute_send_during_tts === 'boolean'
          ? recorder.mute_send_during_tts
          : DEFAULT_POLICY_FLAGS.recorder.mute_send_during_tts,
      };

      const input = rawNested.input && typeof rawNested.input === 'object' ? rawNested.input : null;
      nested.input = {
        require_hotword_to_start: input && typeof input.require_hotword_to_start === 'boolean'
          ? input.require_hotword_to_start
          : DEFAULT_POLICY_FLAGS.input.require_hotword_to_start,
        require_user_gesture_first_visit: input && typeof input.require_user_gesture_first_visit === 'boolean'
          ? input.require_user_gesture_first_visit
          : DEFAULT_POLICY_FLAGS.input.require_user_gesture_first_visit,
      };

      const asr = rawNested.asr && typeof rawNested.asr === 'object' ? rawNested.asr : null;
      let keepWarm = DEFAULT_POLICY_FLAGS.asr.keep_stream_warm_ms;
      if (asr && Number.isFinite(Number(asr.keep_stream_warm_ms))) {
        const parsed = Number(asr.keep_stream_warm_ms);
        if (parsed >= 0) {
          keepWarm = Math.round(parsed);
        }
      }
      const commitOnVad = asr && typeof asr.commit_on_vad_silence === 'boolean'
        ? asr.commit_on_vad_silence
        : DEFAULT_POLICY_FLAGS.asr.commit_on_vad_silence;
      let commitSilence = DEFAULT_POLICY_FLAGS.asr.commit_silence_ms;
      if (asr && Number.isFinite(Number(asr.commit_silence_ms))) {
        const parsed = Number(asr.commit_silence_ms);
        if (parsed >= 0) {
          commitSilence = Math.round(parsed);
        }
      }
      let maxUtterance = DEFAULT_POLICY_FLAGS.asr.max_utterance_ms;
      if (asr && Number.isFinite(Number(asr.max_utterance_ms))) {
        const parsed = Number(asr.max_utterance_ms);
        if (parsed >= 0) {
          maxUtterance = Math.round(parsed);
        }
      }
      const vendorDefaults = DEFAULT_POLICY_FLAGS.asr.vendor || { primary: 'speechmatics', secondary: null };
      const vendorBlock = asr && typeof asr.vendor === 'object' ? asr.vendor : null;
      const vendor = { ...vendorDefaults };
      if (vendorBlock) {
        if (typeof vendorBlock.primary === 'string') {
          const normalized = vendorBlock.primary.trim().toLowerCase();
          if (ASR_VENDOR_OPTIONS.includes(normalized)) {
            vendor.primary = normalized;
          }
        }
        if (vendorBlock.secondary === null) {
          vendor.secondary = null;
        } else if (typeof vendorBlock.secondary === 'string') {
          const normalizedSecondary = vendorBlock.secondary.trim().toLowerCase();
          if (ASR_VENDOR_OPTIONS.includes(normalizedSecondary)) {
            vendor.secondary = normalizedSecondary;
          } else {
            vendor.secondary = vendorDefaults.secondary;
          }
        }
      }

      nested.asr = {
        prearm_on_tts_end: asr && typeof asr.prearm_on_tts_end === 'boolean'
          ? asr.prearm_on_tts_end
          : DEFAULT_POLICY_FLAGS.asr.prearm_on_tts_end,
        keep_stream_warm_ms: keepWarm,
        commit_on_vad_silence: commitOnVad,
        commit_silence_ms: commitSilence,
        max_utterance_ms: maxUtterance,
        vendor,
      };

      const routing = rawNested.routing && typeof rawNested.routing === 'object'
        ? rawNested.routing
        : null;
      const rawVersion = routing && typeof routing.ws_version === 'string'
        ? routing.ws_version.trim()
        : '';
      nested.routing = {
        ws_version: rawVersion && rawVersion.toLowerCase() === 'v2'
          ? 'v2'
          : DEFAULT_POLICY_FLAGS.routing.ws_version,
      };
    }

    const audioSource = source && typeof source === 'object' ? source.audio : null;
    const audioDefaults = DEFAULT_POLICY_FLAGS.audio || { pipeline: { mode: 'pcm16' } };
    const audioPipeline = audioDefaults.pipeline ? { ...audioDefaults.pipeline } : { mode: 'pcm16' };
    if (audioSource && typeof audioSource === 'object') {
      const pipeline = audioSource.pipeline && typeof audioSource.pipeline === 'object'
        ? audioSource.pipeline
        : null;
      if (pipeline && typeof pipeline.mode === 'string') {
        const mode = pipeline.mode.trim().toLowerCase();
        if (AUDIO_PIPELINE_OPTIONS.includes(mode)) {
          audioPipeline.mode = mode;
        }
      }
    }

    policy.policy = nested;
    policy.input = nested && typeof nested.input === 'object' ? { ...nested.input } : {};
    policy.audio = { pipeline: audioPipeline };
    return policy;
  }

  function applyPolicySnapshotFromSource(source, origin) {
    const sanitizedPolicy = sanitizePolicySnapshot(source);
    AppState.policy = sanitizedPolicy;
    updateState({ policy: sanitizedPolicy });
    const snapshotFrame = { type: 'policy.snapshot', policy: sanitizedPolicy, origin: origin || null };
    dispatchFrame(snapshotFrame);
    dispatchFrame({ type: 'config.updated', policy: sanitizedPolicy, origin: origin || null });
    return sanitizedPolicy;
  }

  function sanitizePolicyFrame(frame) {
    const safe = { type: 'policy.interaction' };
    if (frame && typeof frame === 'object') {
      Object.keys(frame).forEach((key) => {
        if (key === 'policy') return;
        safe[key] = frame[key];
      });
    }
    const source = frame && typeof frame === 'object' ? frame.policy : null;
    safe.policy = sanitizePolicySnapshot(source);
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
    } else if (typeof lastPingAt === "number") {
      reference = lastPingAt;
    }
    let rtt = null;
    if (typeof reference === "number") {
      const latency = Math.max(0, now - reference);
      updateState({ latencyMs: latency });
      rtt = latency;
    }
    logStage('client.ws', { outcome: 'pong', rtt_ms: rtt });
  }

  function transcriptFrameAllowed(frame) {
    const type = typeof frame?.type === "string" ? frame.type : "";
    const role = typeof frame?.role === "string" ? frame.role : "";
    const canonicalType = type === "message" || type === "chat.message";
    const canonicalRole = role === "user" || role === "assistant";
    const allow = canonicalType && canonicalRole;
    try {
      console.log(`evt=ui_transcript_filter allow=${allow} type=${type || ""} role=${role || ""}`);
    } catch {}
    return allow;
  }

  function handleChatHistoryFrame(frame) {
    const view = window.TranscriptView;
    const messages = Array.isArray(frame.messages) ? frame.messages : [];
    if (view && typeof view.handleChatMessage === "function" && messages.length) {
      for (const message of messages) {
        if (!transcriptFrameAllowed(message)) {
          continue;
        }
        try {
          view.handleChatMessage(message);
        } catch (err) {
          console.warn("TranscriptView chat history handler error", err);
          break;
        }
      }
    }
  }

  function handleErrorFrame(frame) {
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
    if (code === "schema_invalid" || code === "unknown_type" || code === "bad_utf8") {
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
      close("resume_invalid");
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
      const sanitized = sanitizePolicyFrame(frame);
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
      maybeAutoStart("policy");
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
        handleErrorFrame(frame);
        return;
      } else if (frame.type !== "info") {
        console.error("Expected info frame first, received", frame.type);
        close("bad_info_sequence");
        return;
      }
      if (frame.type === "info") {
        handleInfoFrame(frame);
      }
    } else if (frame.type === "info") {
      handleInfoFrame(frame);
    } else if (frame.type === "server.pong") {
      handlePongFrame(frame);
    } else if (frame.type === "pong") {
      handlePongFrame(frame);
    } else if (frame.type === "error") {
      handleErrorFrame(frame);
    } else if (frame.type === "tts.start") {
      try {
        AppState?.hub?.stopListening?.("tts");
      } catch {}
      stopRecorder("tts_start");
      setAppStateValue("ttsActive", true);
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
      if (AppState.armAfterTtsEnd) {
        schedulePostTtsArm("post_greet");
        setArmAfterTtsEnd(false);
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
      const ar = window.AudioRecorder || null;
      const policy = frame?.policy || {};
      let unifiedArmed = false;
      try {
        if (ar?.setPolicy) ar.setPolicy(policy);
        const vendor = frame?.policy?.asr?.vendor?.primary ?? null;
        const pipeline = frame?.policy?.audio?.pipeline?.mode ?? null;
        const asrInput = frame?.policy?.media?.asr_input ?? null;
        console.info(
          "diag=start_listening_order vendor=%s pipeline=%s asr_input=%s",
          vendor,
          pipeline,
          asrInput,
        );
        if (ar?.start) {
          await ar.start(policy);
          unifiedArmed = true;
        }
      } catch (err) {
        console.warn("AudioRecorder start_listening preflight failed", err);
      }
      try {
        __micAttempts += 1;
        __micChunks = 0;
        __micBytes = 0;
        __micArmedAt = Date.now();
        if (!__turnTraceId) {
          __turnTraceId = `${AppState?.sid || 'sid-unknown'}:${Date.now()}`;
        }
        logMic({ outcome: MIC_OUTCOME.ARMED });
      } catch {}

      setPendingAsrReadyStart({ frame, policy, reason: frame?.reason || frame?.type || "start_listening" });
      if (AppState?.asrReady) {
        try {
          await startStreamingAfterAsrReady("start_listening_asr_ready");
        } catch (err) {
          console.error("Deferred mic start after start_listening failed", err);
        }
      }

      if (unifiedArmed) return;

      let hub;
      try {
        hub = AppState?.hub;
        if (hub && typeof hub.setPolicy === "function") {
          hub.setPolicy(policy);
        }
      } catch (err) {
        console.warn("Hub setPolicy during start_listening failed", err);
      }

      if (hub && typeof hub.prearmListening === "function") {
        try {
          hub.prearmListening(policy);
        } catch (err) {
          console.warn("Hub prearmListening failed", err);
        }
        return;
      }

      if (hub && typeof hub.startListening === "function") {
        // Defer actual streaming until asr.ready fires.
        return;
      }

      console.warn('Legacy input capture deferred until asr.ready', frame);
      return;
    } else if (frame.type === "stop_listening") {
      if (_audioStreaming) {
        const reason = typeof frame?.reason === "string" && frame.reason
          ? frame.reason
          : frame?.type || "stop_listening";
        hubLog("client.stream.off", { reason });
      }
      _audioStreaming = false;
      stopRecorder("server_requested");
      setAsrArmInFlight(false);
      setArmAfterTtsEnd(false);
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
      clearPendingAsrReadyStart("stop_listening");
      __asrReadySeen = false;
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
      hubLog("client.stream.on", { reason });
      await handleInputStartFrame(frame);
    } else if (frame.type === "asr.error" || frame.type === "asr.closed" || frame.type === "asr.reset") {
      __resetAudioHeaderSent();
    } else if (frame.type === "input.stop") {
      if (_audioStreaming) {
        const reason = typeof frame?.reason === "string" && frame.reason
          ? frame.reason
          : frame?.type || "input.stop";
        hubLog("client.stream.off", { reason });
      }
      _audioStreaming = false;
      setListeningState(false);
      emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      stopInputCapture({ reason: "input.stop" });
      clearPendingAsrReadyStart("input.stop");
      __asrReadySeen = false;
      __resetAudioHeaderSent();
    } else if (frame.type === "assistant.await_user") {
      if (_audioStreaming) {
        const reason = typeof frame?.reason === "string" && frame.reason
          ? frame.reason
          : frame?.type || "assistant.await_user";
        hubLog("client.stream.off", { reason });
      }
      _audioStreaming = false;
    } else if (frame.type === "asr.ready") {
      frame = handleAsrReadyFrame(frame) || frame;
      __asrReadySeen = true;
      setAsrArmInFlight(false);
      setArmAfterTtsEnd(false);
      setWsConnected(true);
      AppState.asrReady = true;
      setWsPhase("ready");
      emitConsoleBusEvent("client.asr.ready", { asrReady: true });
      logStage("diag", { label: "asr.ready" });
      logStage("client.asr_arm_clear", { vendor: AppState.asrVendor || DEFAULT_ASR_VENDOR });
      __resetAudioHeaderSent();
      sendAudioHeader(frame);
      try {
        await startRecorderStreaming(frame?.policy || {});
      } catch (err) {
        console.error("Recorder start on asr.ready failed", err);
        logStage("client.mic", {
          outcome: MIC_OUTCOME.ERROR_GUM,
          message: err && err.message ? err.message : String(err || ""),
        });
        setListeningState(false);
      }
    } else if (frame.type === "asr.partial") {
      transcriptFrameAllowed(frame);
    } else if (frame.type === "asr.final") {
      transcriptFrameAllowed(frame);
    } else if (frame.type === "asr.unavailable") {
      const reason = frame && typeof frame.reason === "string" ? frame.reason : "";
      const details = frame && typeof frame.details === "string"
        ? frame.details
        : (frame && typeof frame.detail === "string" ? frame.detail : "");
      console.warn("asr.unavailable", reason, details);
      AppState.asrReady = false;
      AppState.asrVendor = null;
      updateState({ asrReady: false, asrVendor: null });
      stopRecorder("asr_unavailable");
      __resetAudioHeaderSent();
      setAsrArmInFlight(false);
      setArmAfterTtsEnd(false);
      if (typeof AppState.emit === "function") {
        AppState.emit("asrReady", { ready: false, reason, vendor: null });
      }
      clearPendingAsrReadyStart("asr_unavailable");
      __asrReadySeen = false;
      try {
        const hud = window?.HUD || window?.DiagHUD || window?.DiagHud;
        hud?.setState?.("Chat");
      } catch (err) {
        console.warn("Failed to update HUD state after asr.unavailable", err);
      }
      try {
        const view = window.TranscriptView;
        view?.showSystemFromChip?.(
          "Sorry, having issues hearing you right now, but I can absolutely still assist via chat."
        );
      } catch (err) {
        console.warn("Failed to render Chip system message after asr.unavailable", err);
      }
      try {
        window?.Banner?.show?.(
          "Voice temporarily unavailable. You can continue via chat.",
          { level: "warning", ttlMs: 10000 }
        );
      } catch (err) {
        console.warn("Failed to show voice unavailable banner", err);
      }
    } else if (frame.type === "chat.message" || frame.type === "message") {
      if (!transcriptFrameAllowed(frame)) {
        // Keep frame flowing to other listeners without rendering in transcript.
      } else {
        const view = window.TranscriptView;
        if (view && typeof view.handleChatMessage === "function") {
          try {
            view.handleChatMessage(frame);
          } catch (err) {
            console.warn("TranscriptView chat handler error", err);
          }
        }
      }
    } else if (frame.type === "chat.history") {
      handleChatHistoryFrame(frame);
    }
    dispatchFrame(frame);
  }

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

  async function parseFrame(event) {
    const { data } = event;
    if (typeof data === "string") {
      try {
        const frame = JSON.parse(data);
        if (frame && typeof frame.message === "string") {
          if (IGNORED_VENDOR_MESSAGES.has(frame.message)) {
            return;
          }
        }
        const normalizedFrame = normalizeIncomingFrame(frame);
        if (!normalizedFrame) {
          console.warn("Dropping WS frame without recognizable type", frame);
          handleErrorFrame({
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
          send({ type: "client.pong", ts: Date.now(), echo: normalizedFrame.ts });
          return;
        }
        await handleMessageFrame(normalizedFrame);
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
    if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
      const chunk = data instanceof ArrayBuffer
        ? data
        : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      const audioPlayer = getAudioPlayer();
      if (audioPlayer && typeof audioPlayer.enqueueChunk === "function") {
        audioPlayer.enqueueChunk(chunk);
      }
      window.dispatchEvent(new CustomEvent("binary", { detail: chunk }));
      return;
    }
    console.warn("Unknown WS frame type", data);
  }

  function attachSocket(ws) {
    ws.__intentionalClose = false;
    const handlers = {
      open: () => {
        // Write live socket and mark connected so UI gates on ws.open can run
        setWsConnected(true);
        setWsPhase("connected");
        recordLastError(null, null);
        try {
          if (typeof AppState.setState === "function") {
            AppState.setState({ websocket: ws, connectionState: "connected" });
          } else {
            updateState({ websocket: ws, connectionState: "connected" });
          }
        } catch {
          updateState({ websocket: ws, connectionState: "connected" });
        }
        try {
          WSClient._ws = ws;
          WSClient._connected = true;
        } catch {}
        try {
          window.ws = ws;
        } catch {}
        try {
          if (AppState && AppState.hub && typeof AppState.hub.bindSocket === "function") {
            AppState.hub.bindSocket(ws);
          }
        } catch (err) {
          console.warn("AppState.hub.bindSocket open failed", err);
        }
        try {
          if (!Array.isArray(WSClient._queue)) {
            WSClient._queue = [];
          }
          if (WSClient._queue.length) {
            for (const { data, isBinary } of WSClient._queue) {
              WSClient.send(data, { binary: isBinary });
            }
            WSClient._queue.length = 0;
          }
        } catch {}
        try {
          window.dispatchEvent(new CustomEvent("ws.open", { detail: { websocket: ws } }));
        } catch {}
        try {
          if (!WSClient._linkedProofLogged) {
            console.info("evt=ws_linked AppState", !!AppState?.websocket, "WSClient", !!WSClient._ws);
            WSClient._linkedProofLogged = true;
          }
        } catch {}
        startHeartbeat();
        logStage('client.ws', { outcome: 'connected', subprotocol: ws?.protocol || null });
      },
      message: parseFrame,
      error: (event) => {
        console.error("WebSocket error", event);
        const message = event && typeof event?.message === "string" && event.message
          ? event.message
          : "socket_error";
        recordLastError(null, message);
        window.dispatchEvent(new CustomEvent("ws.error", { detail: event }));
      },
      close: (event) => {
        const expected = ws.__intentionalClose === true;
        const detailReason = event && typeof event?.reason === "string" && event.reason
          ? event.reason
          : (expected ? "intentional_close" : "ws_close");
        if (_audioStreaming) {
          const offReason = detailReason || (expected ? "intentional_close" : "ws_close");
          hubLog("client.stream.off", {
            reason: offReason,
            code: typeof event?.code === "number" ? event.code : undefined,
          });
          _audioStreaming = false;
        }
        recordLastError(event && typeof event?.code === "number" ? event.code : null, detailReason);
        setWsConnected(false);
        setWsPhase("disconnected");
        logStage('client.ws', { outcome: 'close', code: event?.code, reason: event?.reason });
        logMic({ outcome: MIC_OUTCOME.STOPPED, reason: event?.reason || (expected ? 'intentional_close' : 'ws_close') });
        try {
          WSClient._connected = false;
          WSClient._ws = null;
          WSClient._linkedProofLogged = false;
        } catch {}
        try {
          window.ws = null;
        } catch {}
        try {
          if (AppState && AppState.hub && typeof AppState.hub.bindSocket === "function") {
            AppState.hub.bindSocket(null);
          }
        } catch (err) {
          console.warn("AppState.hub.bindSocket close failed", err);
        }
        if (socket === ws) {
          socket = null;
          expectInfoFrame = true;
          clearInfoWatchdog();
        }
        clearHeartbeat();
        updateState({ websocket: null });
        let resumed = false;
        if (!expected) {
          resumed = attemptAutoResume();
        }
        if (!resumed) {
          updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
        }
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
      console.warn("WSClient cleanup close error", err);
    }
    if (socket === ws) {
      socket = null;
      expectInfoFrame = true;
      clearInfoWatchdog();
      if (_audioStreaming) {
        const offReason = typeof reason === "string" && reason ? reason : "ws.cleanup";
        hubLog("client.stream.off", { reason: offReason });
      }
      _audioStreaming = false;
      stopInputCapture();
      try {
        if (window.ws === ws) {
          window.ws = null;
        }
      } catch {}
    }
    const client = WSClient;
    if (client && typeof client === "object") {
      client._ws = null;
      client._connected = false;
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
      cleanupSocket(socket, "superseded");
    }
    expectInfoFrame = true;
    startInfoWatchdog();
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

    const wsProtocols = protocols !== undefined ? protocols : SUBPROTOCOL;
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

    const tokenInfo = trackTokenFromUrl(wsUrl);
    const ws = transportFactory(wsUrl, wsProtocols);
    ws.__accessTokenInfo = tokenInfo;
    ws.__handshakeToastShown = false;
    try {
      ws.binaryType = "arraybuffer";
    } catch (err) {
      console.warn("Failed to set WebSocket binaryType", err);
    }

    ws.onopen = () => {
      // Lightweight breadcrumb; keep as console.log
      console.log("WebSocket open", { url: wsUrl, protocol: ws.protocol || wsProtocols });
      logStage('client.ws', { outcome: 'connected', subprotocol: ws?.protocol || (typeof wsProtocols === "string" ? wsProtocols : null) });
      recordClientBannerEvent("ws.socket.open", {
        protocol: truncateBannerString(ws.protocol || (typeof wsProtocols === "string" ? wsProtocols : ""), 48),
      });
      flushClientBannerQueue();
    };

    ws.onerror = (e) => {
      console.error("WebSocket error", e, { readyState: ws.readyState });
      const message = e && typeof e?.message === "string" && e.message
        ? e.message
        : "socket_error";
      recordLastError(null, message);
      maybeShowHandshakeToast(ws, null);
      recordClientBannerEvent("ws.socket.error", {
        ready_state: ws.readyState,
      });
    };

    ws.onclose = (e) => {
      console.error("WebSocket closed", {
        code: e.code,
        reason: e.reason,
        wasClean: e.wasClean,
        readyState: ws.readyState,
      });
      const detailReason = typeof e.reason === "string" && e.reason ? e.reason : "handshake_close";
      recordLastError(typeof e.code === "number" ? e.code : null, detailReason);
      setWsConnected(false);
      setWsPhase("disconnected");
      logStage('client.ws', { outcome: 'close', code: e?.code, reason: e?.reason });
      maybeShowHandshakeToast(ws, e && typeof e.code === "number" ? e.code : null);
      recordClientBannerEvent("ws.socket.close", {
        code: typeof e.code === "number" ? e.code : undefined,
        reason: truncateBannerString(e.reason || "", 160),
        was_clean: Boolean(e.wasClean),
        ready_state: ws.readyState,
      });
    };

    socket = ws;
    updateState({
      connectionState: resumeTokenValue ? "resuming" : "connecting",
      websocket: ws,
      latencyMs: null,
      lastPingAt: null,
      resumeError: null,
      infoFrame: null,
      serverBanner: null
    });
    attachSocket(ws);
    return ws;
  }

  function close(reason = DEFAULT_CLOSE_REASON) {
    if (_audioStreaming) {
      const offReason = typeof reason === "string" && reason ? reason : "client_shutdown";
      hubLog("client.stream.off", { reason: offReason });
    }
    _audioStreaming = false;
    recordClientBannerEvent("ws.close.request", { reason: truncateBannerString(reason || "", 80) });
    stopRecorder(reason || "client_shutdown");
    setAsrArmInFlight(false);
    setArmAfterTtsEnd(false);
    setAppStateValue("ttsActive", false);
    setWsPhase("closing");
    setWsConnected(false);
    const emitResumeInvalid = () => {
      if (reason === "resume_invalid" && typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        try {
          window.dispatchEvent(new CustomEvent("ws.resume_invalid", { detail: { reason } }));
        } catch {}
      }
    };
    if (!socket) {
      setWsPhase("disconnected");
      updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
      emitResumeInvalid();
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
    return Object.prototype.toString.call(payload) === "[object Object]";
  }

  function validatePayloadForSend(payload) {
    if (!isTypedObjectPayload(payload)) {
      return true;
    }
    const { type } = payload;
    if (typeof type === "string" && type.trim().length > 0) {
      return true;
    }
    const keys = Object.keys(payload || {});
    console.warn("WSClient send skipped object payload without type", { keys, payload });
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "missing_type",
        keys: keys.slice(0, 6),
      });
    } catch {}
    try {
      logStage("client.ws", { outcome: "send_skipped_missing_type", keys: keys.slice(0, 6) });
    } catch {}
    return false;
  }

  function send(payload, { binary = false } = {}) {
    const client = (this && typeof this === "object") ? this : WSClient;
    if (!Array.isArray(client._queue)) {
      client._queue = [];
    }
    let data = payload;

    if (!binary && (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload))) {
      console.debug("WSClient.send: binary payload ignored by JSON helper");
      return;
    }

    if (!binary) {
      if (!validatePayloadForSend(data)) {
        return;
      }
      if (!data || typeof data !== "object") {
        console.warn("WSClient.send blocked: missing or invalid type", payload);
        return;
      }
      if (typeof data.type !== "string") {
        console.warn("WSClient.send blocked: missing or invalid type", payload);
        return;
      }
      if (data.type === "audio.header") {
        if (data.format !== "pcm16" ||
            typeof data.sample_rate !== "number" ||
            typeof data.channels !== "number") {
          console.warn("WSClient.send blocked: invalid audio.header schema", payload);
          return;
        }
        data = {
          type: "audio.header",
          format: "pcm16",
          sample_rate: Number(data.sample_rate),
          channels: Number(data.channels)
        };
      }
    }
    let stateSocket = null;
    if (typeof AppState !== "undefined" && AppState) {
      stateSocket = AppState.websocket || null;
      if (!stateSocket && typeof AppState.getState === "function") {
        try {
          const snapshot = AppState.getState();
          stateSocket = snapshot && snapshot.websocket ? snapshot.websocket : null;
        } catch {}
      }
    }
    const live = client._ws || stateSocket;
    try {
      const phase = AppState?.wsPhase || AppState?.connectionState;
      if (!binary && (phase !== "connected" && phase !== "ready" && phase !== "resuming")) {
        console.warn("WSClient.send skipped (phase not ready)", { phase });
        return;
      }
    } catch {}
    if (!live || live.readyState !== WebSocket.OPEN) {
      client._queue.push({ data, isBinary: !!binary });
      console.warn("WSClient.send queued (socket not open)");
      return;
    }
    client._ws = live;
    client._connected = true;
    try { live.binaryType = "arraybuffer"; } catch {}
    if (binary) {
      if (payload instanceof Blob) {
        return payload.arrayBuffer().then((buf) => {
          try {
            live.send(buf);
          } catch (err) {
            console.error("WSClient binary send error", err);
          }
        });
      }
      if (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
        const buffer = payload instanceof ArrayBuffer
          ? payload
          : payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength);
        try {
          live.send(buffer);
        } catch (err) {
          console.error("WSClient binary send error", err);
        }
        return;
      }
    }
    const text = typeof data === "string" ? data : JSON.stringify(data);
    try {
      live.send(text);
    } catch (err) {
      console.error("WSClient send error", err);
    }
  }

  function getBufferedAmount() {
    if (!socket) return 0;
    return socket.bufferedAmount || 0;
  }

  const debug = {
    simulateIncomingFrame(frame) {
      return handleMessageFrame(frame);
    },
    recordPing(ts) {
      lastPingAt = ts;
      updateState({ lastPingAt: ts });
    },
    setTransportFactory(factory) {
      transportFactory = typeof factory === "function" ? factory : transportFactory;
    },
    resetTransportFactory() {
      transportFactory = (url, protocols = SUBPROTOCOL) => new WebSocket(url, protocols);
    }
  };

  WSClient.open = open;
  WSClient.close = close;
  WSClient.send = send;
  WSClient.sendBinary = sendBinary;
  WSClient.getBufferedAmount = getBufferedAmount;
  WSClient.requestAsrArm = requestAsrArm;
  WSClient.requestAsrClose = requestAsrClose;
  Object.defineProperty(WSClient, "socket", {
    configurable: true,
    enumerable: true,
    get() {
      return socket;
    }
  });
  WSClient.isConnected = function isConnected() {
    return !!socket && socket.readyState === WebSocket.OPEN && AppState.getState().connectionState === "connected";
  };
  WSClient.__debug = debug;
  window.WSClient = WSClient;
  WSClient._ws = WSClient._ws || null;
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("ws.close", () => {
      __resetAudioHeaderSent();
    });
    window.addEventListener("ws.resume_invalid", () => {
      __resetAudioHeaderSent();
    });
  }
  if (typeof WSClient._linkedProofLogged !== "boolean") {
    WSClient._linkedProofLogged = false;
  }
})();
