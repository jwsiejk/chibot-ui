// CLEAN BUILD (2025-11-06): PCM16@16k mono ONLY; no MediaRecorder/WebM/Opus/Deepgram; no wake word.
/* __BUILD_MARKER__: FULL_DUPLEX_01 */
import { initVAD } from "./audio/vad_client.js";
import { initPcmSender } from "./audio/pcm_sender.js";
(() => {
  const HEARTBEAT_INTERVAL_MS = 20000;
  const DEFAULT_CLOSE_REASON = "client_shutdown";
  const SUBPROTOCOL = "chat.v2";
  const INFO_DEADLINE_MS = 20000;
  const TOKEN_EXPIRY_MS = 60 * 1000;
  const TOAST_STYLE_ID = "wsclient-toast-styles";
  const TOAST_STYLE_TEXT = "#toast-root.toast-container{position:fixed;bottom:24px;right:24px;display:flex;flex-direction:column;gap:12px;z-index:4000;pointer-events:none;}#toast-root .toast{pointer-events:auto;min-width:240px;max-width:340px;padding:14px 18px;border-radius:12px;background:rgba(220,38,38,0.92);color:#fff;box-shadow:0 18px 40px rgba(12,14,24,0.35);font-family:\"Inter\",system-ui,-apple-system,\"Segoe UI\",sans-serif;backdrop-filter:blur(12px);display:flex;flex-direction:column;gap:6px;transition:opacity 160ms ease,transform 160ms ease;}#toast-root .toast.toast-exit{opacity:0;transform:translateY(12px);}#toast-root .toast-body{font-size:0.88rem;line-height:1.4;}";
  const MAX_GATE_SILENCE_MS = 3000;
  // server_no_speech_timeout_ms should be ≥ 2 × MAX_GATE_SILENCE_MS to let the client close the turn cleanly.
  const CLIENT_VAD_POLICY = Object.freeze({
    enable: true,
    sensitivity: 0.60,
    min_speech_ms: 160,
    min_silence_ms: 500,
    hold_ms: 250,
    echo_suppression_db: 10,
    tts_threshold_boost_db: 12,
    debounce_ms: 40,
    // Enable true gating; preroll remains handled internally by the VAD.
    stream_gate: "gate",          // keep gating if you want to save bandwidth
    max_gate_silence_ms: MAX_GATE_SILENCE_MS,
    // Optional: per-deployment configurable warmup in ms
    warmup_ms: 1200,
    // If your input is very quiet, allow threshold override via policy
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
  const PCM_TARGET_SAMPLE_RATE = 16000;
  const DEFAULT_ASR_VENDOR = 'gcp';
  const WS_READY_PHASES = new Set(['connected', 'ready', 'resuming']);

  class PcmRingBuffer {
    constructor({ millis, sampleRate, channels = 1 }) {
      this.sampleRate = sampleRate;
      this.channels = channels;
      this.maxSamples = Math.ceil((millis / 1000) * sampleRate) * channels;
      this.buf = new Int16Array(this.maxSamples);
      this.write = 0;
      this.filled = false;
    }
    push(int16Chunk) {
      const data = int16Chunk;
      const n = data.length;
      if (n >= this.maxSamples) {
        this.buf.set(data.subarray(n - this.maxSamples));
        this.write = 0;
        this.filled = true;
        return;
      }
      const end = this.write + n;
      if (end <= this.maxSamples) {
        this.buf.set(data, this.write);
      } else {
        const first = this.maxSamples - this.write;
        this.buf.set(data.subarray(0, first), this.write);
        this.buf.set(data.subarray(first), 0);
      }
      this.write = (end % this.maxSamples);
      if (this.write === 0) this.filled = true;
    }
    tailMillis(millis) {
      const samples = Math.min(this.maxSamples, Math.ceil((millis / 1000) * this.sampleRate) * this.channels);
      const out = new Int16Array(samples);
      if (!this.filled && this.write === 0) return [];
      const start = (this.write - samples + this.maxSamples) % this.maxSamples;
      if (start + samples <= this.maxSamples) {
        out.set(this.buf.subarray(start, start + samples), 0);
      } else {
        const first = this.maxSamples - start;
        out.set(this.buf.subarray(start), 0);
        out.set(this.buf.subarray(0, samples - first), first);
      }
      return [out];
    }
    clear() { this.write = 0; this.filled = false; }
  }

  // ---- Debug toggles (runtime-settable) ----
  function dbg(key, fallback = false) {
    try {
      return !!(window.AppState?.debug && window.AppState.debug[key]);
    } catch {
      return fallback;
    }
  }

  let __micAttempts = 0;
  let __micChunks = 0;
  let __micBytes = 0;
  let __micArmedAt = 0;     // ms since epoch
  let __micPermissionGranted = false;
  let __micRecordingStartAt = null;
  let __micFirstChunkBreadcrumbSent = false;
  let __firstChunkSeen = false;
  let __armingGraceUntil = 0; // ms epoch; brief window after capture start
  let __turnTraceId = null; // optional trace id per turn (sid + timestamp)
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

  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading WSClient");
  }
  const ASR_RATE = (AppState?.targetSampleRate || 16000);
  if (typeof window !== "undefined") {
    window.__pcmRing ||= new PcmRingBuffer({ millis: 1500, sampleRate: ASR_RATE, channels: 1 });
  }
  const pcmRing = (typeof window !== "undefined") ? window.__pcmRing : null;
  if (typeof AppState._recoverPrimePending === "undefined") {
    AppState._recoverPrimePending = false;
  }
  const FEATURE_LEGACY_POLICY = Boolean(window.FEATURE_LEGACY_POLICY ?? false);
  const P0 = AppState && typeof AppState.policy === "object" ? AppState.policy : {};
  const DEFAULT_POLICY_VAD = { warmup_ms: 1200, sender_gate_on_tts: true };
  const DEFAULT_POLICY_WATCHDOG = {
    partial_wait_ms_first_turn: 3500,
    partial_wait_ms: 2500,
  };
  const DEFAULT_POLICY_STATUS = { require_active_turn: true };

  const cloneValue = (value) => {
    if (Array.isArray(value)) {
      return value.slice();
    }
    if (value && typeof value === "object") {
      return { ...value };
    }
    return value;
  };

  if (FEATURE_LEGACY_POLICY) {
    const P0Policy = P0 && typeof P0.policy === "object" && P0.policy
      ? { ...P0.policy }
      : {};
    AppState.policy = {
      ...P0,
      auto_record_after_greet: P0.auto_record_after_greet ?? true,
      require_user_gesture_first_visit: P0.require_user_gesture_first_visit ?? false,
      tts_gate_enabled: P0.tts_gate_enabled ?? true,
      // REMOVED: autostart_retry_on / autostart_backoff_ms / autostart_max_attempts
      autostart_retry_on: Array.isArray(P0.autostart_retry_on)
        ? P0.autostart_retry_on.slice()
        : ["asrReady", "ttsEnded", "turnState:Ready"],
      autostart_backoff_ms: Array.isArray(P0.autostart_backoff_ms)
        ? P0.autostart_backoff_ms.slice()
        : [0, 300, 1000],
      autostart_max_attempts: Number.isFinite(P0.autostart_max_attempts)
        ? P0.autostart_max_attempts
        : 5,
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
  } else {
    const basePolicy = {
      auto_record_after_greet: P0?.auto_record_after_greet ?? true,
      require_user_gesture_first_visit: P0?.require_user_gesture_first_visit ?? false,
      tts_gate_enabled: P0?.tts_gate_enabled ?? true,
      // REMOVED: autostart_retry_on / autostart_backoff_ms / autostart_max_attempts
      autostart_retry_on: Array.isArray(P0?.autostart_retry_on)
        ? P0.autostart_retry_on.filter((item) => typeof item === "string" && item)
        : ["asrReady", "ttsEnded", "turnState:Ready"],
      autostart_backoff_ms: Array.isArray(P0?.autostart_backoff_ms)
        ? P0.autostart_backoff_ms
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value >= 0)
        : [0, 300, 1000],
      autostart_max_attempts: Number.isFinite(P0?.autostart_max_attempts)
        ? Number(P0.autostart_max_attempts)
        : 5,
      show_tap_to_speak_cta_after_ms: Number.isFinite(P0?.show_tap_to_speak_cta_after_ms)
        ? Number(P0.show_tap_to_speak_cta_after_ms)
        : 2000,
      reopen_asr_on_idle: P0?.reopen_asr_on_idle ?? true,
    };

    const sanitizedPolicy = {};
    if (P0 && typeof P0 === "object") {
      Object.keys(P0).forEach((key) => {
        const value = P0[key];
        if (typeof value === "undefined") {
          return;
        }
        sanitizedPolicy[key] = cloneValue(value);
      });
    }

    const vadSource = P0 && typeof P0.vad === "object" ? P0.vad : {};
    const watchdogSource = P0 && typeof P0.watchdog === "object" ? P0.watchdog : {};
    const uiSource = P0 && typeof P0.ui === "object" ? P0.ui : {};
    const statusSource = uiSource && typeof uiSource.status === "object" ? uiSource.status : {};

    sanitizedPolicy.vad = { ...DEFAULT_POLICY_VAD, ...vadSource };
    sanitizedPolicy.watchdog = { ...DEFAULT_POLICY_WATCHDOG, ...watchdogSource };
    sanitizedPolicy.ui = { ...uiSource };
    sanitizedPolicy.ui.status = { ...DEFAULT_POLICY_STATUS, ...statusSource };
    if (!Number.isFinite(sanitizedPolicy.version)) {
      sanitizedPolicy.version = 2;
    }
    if (typeof sanitizedPolicy._normalized_from !== "string") {
      sanitizedPolicy._normalized_from = "v2";
    }

    const normalizedPolicy = { ...basePolicy };
    Object.keys(sanitizedPolicy).forEach((key) => {
      const value = sanitizedPolicy[key];
      if (typeof value === "undefined") {
        return;
      }
      normalizedPolicy[key] = cloneValue(value);
    });

    AppState.policy = normalizedPolicy;
  }

  function installClientVadPolicySnapshot() {
    const policyRoot = AppState.policy && typeof AppState.policy === "object"
      ? AppState.policy
      : (AppState.policy = {});
    const ensureVadBlock = (target) => {
      if (!target || typeof target !== "object") {
        return;
      }
      const existingVad = target.vad && typeof target.vad === "object" ? target.vad : {};
      const existingClient = existingVad.client && typeof existingVad.client === "object"
        ? existingVad.client
        : {};
      // Merge: runtime/client overrides survive; our defaults fill gaps.
      target.vad = { ...existingVad, client: { ...CLIENT_VAD_POLICY, ...existingClient } };
    };
    ensureVadBlock(policyRoot);
    if (FEATURE_LEGACY_POLICY) {
      if (!policyRoot.policy || typeof policyRoot.policy !== "object") {
        policyRoot.policy = {};
      }
      ensureVadBlock(policyRoot.policy);
    }
    // Hard-disable any legacy hotword gate, regardless of incoming policy.
    const ensureInput = (root) => {
      if (!root || typeof root !== "object") {
        return;
      }
      const existingInput = root.input && typeof root.input === "object" ? root.input : {};
      root.input = { ...existingInput, require_hotword_to_start: false };
    };
    ensureInput(policyRoot);
    if (FEATURE_LEGACY_POLICY) {
      ensureInput(policyRoot.policy);
    }
  }

  installClientVadPolicySnapshot();

  // Derive POLICY *after* defaults are merged in
  const POLICY = AppState && typeof AppState.policy === "object" ? AppState.policy : {};
  const POLICY_VAD = POLICY?.vad || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.vad : {});
  const POLICY_WATCHDOG = POLICY?.watchdog || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.watchdog : {});
  const POLICY_STATUS = POLICY?.ui?.status
    || (FEATURE_LEGACY_POLICY ? POLICY?.policy?.ui?.status : undefined);
  const WATCHDOG_FIRST_MS = Number(
    POLICY_WATCHDOG?.partial_wait_ms_first_turn ?? DEFAULT_POLICY_WATCHDOG.partial_wait_ms_first_turn,
  );
  const SENDER_GATE_ON_TTS = Boolean(
    POLICY_VAD?.sender_gate_on_tts ?? DEFAULT_POLICY_VAD.sender_gate_on_tts,
  );
  const REQUIRE_ACTIVE_TURN = Boolean(
    POLICY_STATUS?.require_active_turn ?? DEFAULT_POLICY_STATUS.require_active_turn,
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

  const WSClient = window.WSClient = window.WSClient || {};
  if (typeof window !== "undefined" && typeof window.ws === "undefined") {
    window.ws = null;
  }
  const wsEventEmitter = WSClient.__events = WSClient.__events || createEventEmitter();
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
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
  WSClient.__firstChunkSeen = () => __firstChunkSeen === true;
  const getAudioPlayer = () => window.AudioPlayer;

  WSClient.waitForOnce = function waitForOnce(type, predicate, timeoutMs = 2000) {
    return new Promise((resolve, reject) => {
      let done = false;
      const timer = setTimeout(() => {
        if (done) {
          return;
        }
        finalize(reject, new Error(`waitForOnce timeout: ${type}`));
      }, timeoutMs);

      function finalize(callback, value) {
        if (done) {
          return;
        }
        done = true;
        clearTimeout(timer);
        try {
          WSClient.off('frame', onFrame);
        } catch {}
        callback(value);
      }

      function onFrame(frame) {
        if (done) {
          return;
        }
        if (!frame || frame.type !== type) {
          return;
        }
        try {
          if (!predicate || predicate(frame)) {
            finalize(resolve, frame);
          }
        } catch (err) {
          finalize(reject, err);
        }
      }

      WSClient.on('frame', onFrame);
    });
  };

  // ---- Type guards / helpers ----
  function isTypedArray(value) {
    return ArrayBuffer.isView(value) && !(value instanceof DataView);
  }

  function toArrayBuffer(value) {
    if (value instanceof ArrayBuffer) {
      return value;
    }
    if (isTypedArray(value)) {
      if (value.byteLength === value.buffer.byteLength && value.byteOffset === 0) {
        return value.buffer;
      }
      try {
        return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
      } catch {
        return null;
      }
    }
    return null;
  }

  function wsOpen() {
    const ws = WSClient._ws || window.ws;
    return ws && ws.readyState === WebSocket.OPEN ? ws : null;
  }

  function isControlFrame(frame) {
    if (!frame || typeof frame !== "object") return false;
    const t = typeof frame.type === "string" ? frame.type : "";
    return t === "input.start" || t === "input.stop" || t === "audio.header" || t === "ping" || t === "pong";
  }

  // ---- Turn opener (idempotent + retry) ----
  let __turnOpen = false, __turnOpenAt = 0;
  async function openTurnOnce(reason) {
    if (__turnOpen) return true;
    const ws = () => (WSClient?._ws || window.ws);
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
    if (!__turnOpen) return;
    __turnOpen = false;
    __turnOpenAt = 0;
    try {
      hubLog("client.turn.intent", { action: "close", reason: reason || "reset" });
    } catch {}
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
        const live = wsOpen();
        connected = !!live;
      }
    } catch {}
    if (!connected && typeof WSClient?._connected === "boolean") connected = WSClient._connected;
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
  function _canCaptureNow() {
    if (dbg("audio_safe_mode") || dbg("force_capture")) return true;
    // During arming (or until first chunk), don’t block the stream
    if (!__firstChunkSeen || Date.now() < __armingGraceUntil) return true;
    const s = window.AppState || {};
    const ws = WSClient?._ws || window.ws;
    if (!(!!ws && ws.readyState === WebSocket.OPEN)) {
      return false;
    }
    if (s.tts) {
      return false;
    }
    // Simplified gate check: listening state must be true AND not paused
    return _warming() || (s.listening && !senderPaused); 
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
  const PCM_TARGET_BATCH_MS = 60;
  const PCM_FLUSH_TIMER_MS = 50;
  const SILENCE_FRAME_MS = 20;
  const SILENCE_REQUIRED_FRAMES = 5;
  const SILENCE_RMS_THRESHOLD = 0.012;
  const SILENCE_PREROLL_MS = 100;
  const SILENCE_IDLE_TICK_MS = 5000;

  let pcmSender = null;
  let pcmSenderInitPromise = null;
  let pcmLastSeq = 0;
  let pcmSampleRate = PCM_TARGET_SAMPLE_RATE;
  let pcmHardwareSampleRate = null;
  let silenceConsecutiveFrames = 0;
  let silenceSuppressed = false;
  let silenceLastIdleTickAt = 0;

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

  function getVadPolicySnapshot() {
    try {
      const root = (typeof AppState === "object" && AppState && typeof AppState.policy === "object")
        ? AppState.policy
        : null;
      const client = root?.vad?.client;
      if (client && typeof client === "object") {
        // Shallow merge: runtime overrides hard-coded safe defaults
        return { vad: { client: { ...CLIENT_VAD_POLICY, ...client } } };
      }
    } catch {}
    return CLIENT_VAD_POLICY_ROOT;
  }

  // Resolve warmup once per session start (policy or default)
  function getWarmupMs() {
    try {
      const snap = getVadPolicySnapshot();
      const ms = snap?.vad?.client?.warmup_ms;
      if (Number.isFinite(ms) && ms >= 0 && ms <= 10000) return ms;
    } catch {}
    return CLIENT_VAD_POLICY.warmup_ms; // 1200 fallback
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
      if (FEATURE_LEGACY_POLICY) {
        const nestedRoot = policyRoot.policy && typeof policyRoot.policy === "object" ? policyRoot.policy : null;
        if (nestedRoot) {
          const nested = nestedRoot.vad && typeof nestedRoot.vad === "object" ? nestedRoot.vad : null;
          if (nested && typeof nested.client === "object") {
            return nested.client;
          }
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
      if (typeof pcmRing?.clear === 'function') {
        try {
          pcmRing.clear();
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
  const AUDIO_KEEPALIVE_MS = 20000;

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

  let micKeepaliveTimerId = null;
  let micLastChunkAt = 0;

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
    if (WS_READY_PHASES.has(phase)) {
      flushQueuedFrames();
    }
  }

  function flushQueuedFrames(client = WSClient) {
    const target = client || WSClient;
    if (!Array.isArray(target?._queue) || target._queue.length === 0) {
      return;
    }
    const liveSocket = target._ws || AppState?.websocket || null;
    if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) {
      return;
    }
    const phase = AppState?.wsPhase || AppState?.connectionState;
    if (!WS_READY_PHASES.has(phase)) {
      return;
    }
    const pending = target._queue.splice(0, target._queue.length);
    for (const entry of pending) {
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const { data, isBinary, options } = entry;
      try {
        if (isBinary) {
          const result = sendBinary(data, options || {});
          if (result && typeof result.then === "function") {
            result.catch((err) => {
              console.warn("WSClient queued binary send failed", err);
            });
          }
          continue;
        }
        send.call(target, data, { binary: isBinary, skipPhaseCheck: true });
      } catch (err) {
        console.warn("WSClient queue flush send failed", err);
      }
    }
  }

  function resetRecorderTelemetry() {
    setAppStateValue("chunkCount", 0);
    setAppStateValue("lastChunkTs", null);
    __firstChunkSeen = false;
    __armingGraceUntil = 0;
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
          WSClient.sendJSON({ type: "client.ping" });
          logStage("client.ping", { lane: "mic" });
        } catch (err) {
          console.warn("client.ping send failed", err);
        }
      }
      scheduleAudioKeepalive();
    }, AUDIO_KEEPALIVE_MS);
  }

  function handlePcmError(err) {
    try {
      logStage("client.pcm", { outcome: "send_error", message: err?.message || "pcm_sender" });
    } catch (_) {}
    if (err) {
      console.warn("pcm.sender.error", err);
    }
  }

  function handleSampleRate(value, meta = {}) {
    const hardwareRate = Number(value);
    if (Number.isFinite(hardwareRate) && hardwareRate > 0) {
      pcmHardwareSampleRate = hardwareRate;
      console.log("client.pcm.hardware_sample_rate", hardwareRate);
    }
    const targetRate = Number(meta?.targetSampleRate);
    if (Number.isFinite(targetRate) && targetRate > 0) {
      pcmSampleRate = targetRate;
    } else {
      pcmSampleRate = PCM_TARGET_SAMPLE_RATE;
    }
    console.log("client.pcm.target_sample_rate", pcmSampleRate);
    console.log("client.pcm.sample_rate", pcmSampleRate);
    if (AppState && typeof AppState === "object") {
      const audioState = AppState.audio && typeof AppState.audio === "object"
        ? { ...AppState.audio }
        : {};
      if (Number.isFinite(pcmSampleRate)) {
        audioState.sampleRate = pcmSampleRate;
        audioState.targetSampleRate = pcmSampleRate;
      }
      if (Number.isFinite(pcmHardwareSampleRate)) {
        audioState.hardwareSampleRate = pcmHardwareSampleRate;
      }
      AppState.audio = audioState;
      updateState({ audio: audioState });
    }
  }

  function* chunk20ms(int16, sampleRate) {
    const size = Math.round(sampleRate * 0.02);
    if (!Number.isFinite(size) || size <= 0) {
      return;
    }
    for (let i = 0; i + size <= int16.length; i += size) {
      yield int16.subarray(i, i + size);
    }
  }

  function computeRms(int16) {
    if (!(int16 instanceof Int16Array) || !int16.length) {
      return 0;
    }
    let sumSq = 0;
    for (let i = 0; i < int16.length; i += 1) {
      const sample = int16[i] / 32768;
      sumSq += sample * sample;
    }
    return Math.sqrt(sumSq / int16.length);
  }

  function resetSilenceSuppression() {
    silenceConsecutiveFrames = 0;
    silenceSuppressed = false;
    silenceLastIdleTickAt = 0;
    setSenderPauseReason("silence_gate", false);
  }

  function maybeSendSilenceIdleTick(now) {
    if (!silenceSuppressed) {
      silenceLastIdleTickAt = 0;
      return;
    }
    if (!Number.isFinite(now) || now <= 0) {
      return;
    }
    if (silenceLastIdleTickAt && now - silenceLastIdleTickAt < SILENCE_IDLE_TICK_MS) {
      return;
    }
    const ws = socket || WSClient?._ws || null;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      WSClient.sendJSON({ type: "client.idle", lane: "mic", ts: now });
      silenceLastIdleTickAt = now;
    } catch (err) {
      try { console.warn("client.idle send failed", err); } catch (_) {}
    }
  }

  function evaluateSilenceSuppression(int16, sampleRate, now) {
    if (!(int16 instanceof Int16Array) || !int16.length) {
      return false;
    }
    const rate = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : ASR_RATE;
    const frameSamples = Math.max(1, Math.round((SILENCE_FRAME_MS / 1000) * rate));
    let resumeTriggered = false;
    let framesEvaluated = 0;
    if (frameSamples > 0 && int16.length >= frameSamples) {
      for (const frame of chunk20ms(int16, rate)) {
        if (!(frame instanceof Int16Array) || !frame.length) {
          continue;
        }
        framesEvaluated += 1;
        const frameRms = computeRms(frame);
        if (frameRms >= SILENCE_RMS_THRESHOLD) {
          if (silenceSuppressed) {
            resumeTriggered = true;
          }
          silenceConsecutiveFrames = 0;
        } else {
          silenceConsecutiveFrames += 1;
          if (!silenceSuppressed && silenceConsecutiveFrames >= SILENCE_REQUIRED_FRAMES) {
            silenceSuppressed = true;
            setSenderPauseReason("silence_gate", true);
          }
        }
      }
    }
    if (!framesEvaluated) {
      const frameRms = computeRms(int16);
      if (frameRms >= SILENCE_RMS_THRESHOLD) {
        if (silenceSuppressed) {
          resumeTriggered = true;
        }
        silenceConsecutiveFrames = 0;
      } else {
        silenceConsecutiveFrames += 1;
        if (!silenceSuppressed && silenceConsecutiveFrames >= SILENCE_REQUIRED_FRAMES) {
          silenceSuppressed = true;
          setSenderPauseReason("silence_gate", true);
        }
      }
    }
    if (resumeTriggered) {
      silenceSuppressed = false;
      silenceConsecutiveFrames = 0;
      silenceLastIdleTickAt = 0;
      setSenderPauseReason("silence_gate", false);
      return true;
    }
    if (silenceSuppressed) {
      maybeSendSilenceIdleTick(now);
    } else {
      silenceLastIdleTickAt = 0;
    }
    return false;
  }

  function getSilencePreroll(sampleRate) {
    if (!pcmRing || typeof pcmRing.tailMillis !== "function") {
      return [];
    }
    const rate = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : ASR_RATE;
    const desiredSamples = Math.max(0, Math.round((SILENCE_PREROLL_MS / 1000) * rate));
    const tails = pcmRing.tailMillis(SILENCE_PREROLL_MS);
    if (!Array.isArray(tails) || !tails.length) {
      return [];
    }
    const payloads = [];
    for (const tail of tails) {
      if (!(tail instanceof Int16Array) || !tail.length) {
        continue;
      }
      if (desiredSamples > 0 && tail.length > desiredSamples) {
        payloads.push(tail.subarray(tail.length - desiredSamples));
      } else {
        payloads.push(tail);
      }
    }
    return payloads;
  }

  function sendPrerollAndChunk(prerollChunks, chunk, sampleRate) {
    const payloads = [];
    if (Array.isArray(prerollChunks) && prerollChunks.length) {
      payloads.push(...prerollChunks);
    }
    if (chunk instanceof Int16Array && chunk.length) {
      payloads.push(chunk);
    }
    if (!payloads.length) {
      return;
    }
    for (const payload of payloads) {
      if (!(payload instanceof Int16Array) || !payload.length) {
        continue;
      }
      const sr = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : ASR_RATE;
      if (pcmSender && typeof pcmSender.sendImmediate === "function") {
        try {
          pcmSender.sendImmediate(payload, { chunkCount: 1, sampleRate: sr });
          continue;
        } catch (err) {
          try { console.warn("pcmSender.sendImmediate failed", err); } catch (_) {}
        }
      }
      try {
        WSClient.sendAudioChunk(payload, { lane: "mic" });
        handlePcmSend(payload, { chunkCount: 1, sampleRate: sr });
      } catch (err) {
        try { console.warn("preroll send fallback failed", err); } catch (_) {}
      }
    }
  }

  function clearPartialWatchdog() {
    if (partialWatchdogTimer) {
      clearTimeout(partialWatchdogTimer);
      partialWatchdogTimer = null;
    }
    partialWatchdogDeadline = 0;
  }

  function schedulePartialWatchdog(reason) {
    if (!_audioStreaming || asrRecovering) {
      return;
    }
    const firstMs = Number(POLICY_WATCHDOG?.partial_wait_ms_first_turn ?? DEFAULT_POLICY_WATCHDOG.partial_wait_ms_first_turn);
    const nextMs = Number(POLICY_WATCHDOG?.partial_wait_ms ?? DEFAULT_POLICY_WATCHDOG.partial_wait_ms);
    let ms = null;
    if (partialWatchdogFirstTurn && Number.isFinite(firstMs) && firstMs > 0) {
      ms = firstMs;
    } else if (Number.isFinite(nextMs) && nextMs > 0) {
      ms = nextMs;
    }
    if (!Number.isFinite(ms) || ms <= 0) {
      return;
    }
    clearPartialWatchdog();
    partialWatchdogDeadline = Date.now() + ms;
    partialWatchdogTimer = setTimeout(() => {
      partialWatchdogTimer = null;
      partialWatchdogDeadline = 0;
      void recoverFromAsrFault("partial_watchdog");
    }, ms);
    partialWatchdogFirstTurn = false;
    try {
      hubLog("client.watchdog.partial_arm", { reason, ms });
    } catch {}
  }

  function primeAsrStreamFromRing(sid) {
    if (!pcmRing || typeof pcmRing.tailMillis !== 'function') {
      return;
    }
    const tails = pcmRing.tailMillis(900);
    if (!Array.isArray(tails) || !tails.length) {
      return;
    }
    const sessionId = sid || `${Date.now()}`;
    if (primedSessionIds.has(sessionId)) {
      return;
    }
    try {
      for (const tail of tails) {
        if (!(tail instanceof Int16Array)) {
          continue;
        }
        for (const chunk of chunk20ms(tail, ASR_RATE)) {
          if (chunk && chunk.length) {
            WSClient.sendAudioChunk(chunk);
          }
        }
      }
      primedSessionIds.add(sessionId);
      if (primedSessionIds.size > 32) {
        const oldest = primedSessionIds.values().next();
        if (!oldest.done && oldest.value !== sessionId) {
          primedSessionIds.delete(oldest.value);
        }
      }
    } catch (err) {
      try { console.warn("primeAsrStreamFromRing failed", err); } catch (_) {}
    }
  }

  function handlePcmFrame(frame, meta = {}) {
    if (!frame || !frame.length) {
      return;
    }
    let wire = frame;
    if (wire instanceof ArrayBuffer) {
      wire = new Int16Array(wire);
    } else if (ArrayBuffer.isView(wire) && !(wire instanceof Int16Array)) {
      const view = wire;
      if (view.BYTES_PER_ELEMENT === 2) {
        wire = new Int16Array(view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength));
      } else {
        return;
      }
    }
    if (!(wire instanceof Int16Array)) {
      return;
    }
    // If your pipeline might deliver non-wire format, normalize here.
    // Ensure 'wire' is Int16, mono, and at ASR_RATE (16k by default).
    // Example (uncomment/adapt if needed):
    // if (!(wire instanceof Int16Array)) { wire = float32ToInt16(wire); }
    // if ((meta?.sampleRate && meta.sampleRate !== ASR_RATE) || (AppState?.micSampleRate && AppState.micSampleRate !== ASR_RATE)) {
    //   wire = resampleInt16Mono(wire, meta?.sampleRate || AppState.micSampleRate || ASR_RATE, ASR_RATE);
    // }

    const metaSampleRate = Number(meta.sampleRate);
    if (Number.isFinite(metaSampleRate) && metaSampleRate > 0) {
      pcmSampleRate = metaSampleRate;
    }
    pcmLastSeq = Number.isFinite(meta.seq) ? Number(meta.seq) : pcmLastSeq;

    const currentSampleRate = Number.isFinite(pcmSampleRate) && pcmSampleRate > 0 ? pcmSampleRate : ASR_RATE;
    const now = Date.now();

    let resumeTriggered = false;
    let prerollChunks = [];
    if (_audioStreaming) {
      resumeTriggered = evaluateSilenceSuppression(wire, currentSampleRate, now);
      if (resumeTriggered) {
        prerollChunks = getSilencePreroll(currentSampleRate);
      }
    }

    try {
      if (typeof pcmRing?.push === 'function') {
        pcmRing.push(wire);
      }
    } catch (e) {
      console.warn("pcmRing.push failed", e);
    }

    if (!_audioStreaming) {
      return;
    }

    if (resumeTriggered) {
      sendPrerollAndChunk(prerollChunks, wire, currentSampleRate);
    }

    if (!__firstChunkSeen) {
      __firstChunkSeen = true;
      let firstFrameMs = null;
      if (typeof __micRecordingStartAt === "number") {
        firstFrameMs = Math.max(0, Math.round(now - __micRecordingStartAt));
      }
      const firstFrameDetail = {
        seq: pcmLastSeq,
        bytes: wire.byteLength,
      };
      if (firstFrameMs !== null) {
        firstFrameDetail.ms_since_recording_start = firstFrameMs;
      }
      try { hubLog("client.pcm.first_frame", firstFrameDetail); } catch {}
      try { logStage("client.audio_first_chunk", { bytes: wire.byteLength }); } catch {}
    }

    micLastChunkAt = now;
    scheduleAudioKeepalive();
    recordRecorderChunk(now);

    const frameTimestamp = Number.isFinite(meta.timestamp)
      ? meta.timestamp
      : ((typeof performance !== "undefined" && typeof performance.now === "function")
        ? performance.now()
        : now);

    if (vadController && typeof vadController.onPcmFrame === "function") {
      try {
        vadController.onPcmFrame(wire.buffer, frameTimestamp);
      } catch (err) {
        try { console.warn("VAD frame processing failed", err); } catch (_) {}
      }
    }

    let sumSq = 0;
    for (let i = 0; i < wire.length; i += 1) {
      const sample = wire[i] / 32768;
      sumSq += sample * sample;
    }
    if (wire.length) {
      const rms = Math.sqrt(sumSq / wire.length);
      if (AppState && typeof AppState === "object") {
        AppState.micRms = rms;
      }
      window.StatusBar?.updateMeter?.(rms);
    }
  }

  function handlePcmSend(chunk, meta = {}) {
    if (!(chunk instanceof Int16Array) || !chunk.length) {
      return;
    }
    const chunkCount = Number.isFinite(meta.chunkCount) ? Number(meta.chunkCount) : 1;
    const seq = Number.isFinite(meta.seq) ? Number(meta.seq) : pcmLastSeq;
    const metaSampleRate = Number(meta.sampleRate);
    if (Number.isFinite(metaSampleRate) && metaSampleRate > 0) {
      pcmSampleRate = metaSampleRate;
    }
    const bytes = chunk.byteLength;
    logStage("client.audio_chunk_send", { seq, bytes, batch_chunks: chunkCount });
    __micChunks = (Number.isFinite(__micChunks) ? __micChunks : 0) + chunkCount;
    __micBytes = (Number.isFinite(__micBytes) ? __micBytes : 0) + bytes;
    if (pcmSampleRate && Number.isFinite(pcmSampleRate)) {
      const samplesPerMs = pcmSampleRate / 1000;
      if (samplesPerMs > 0 && ((Math.random() * 50) | 0) === 0) {
        const ms_est = Math.round(chunk.length / samplesPerMs);
        hubLog("client.pcm.flush", { samples: chunk.length, ms_est, ws_state: (socket || WSClient?._ws)?.readyState });
      }
    }
    scheduleAudioKeepalive();
  }

  function updatePcmSenderState() {
    if (!pcmSender || typeof pcmSender.setEnabled !== "function") {
      return;
    }
    const asrReady = Boolean(AppState?.asrReady);
    const turnActive = AppState && Object.prototype.hasOwnProperty.call(AppState, "turnActive")
      ? Boolean(AppState.turnActive)
      : true;
    const shouldSend = Boolean(_audioStreaming && !senderPaused && _canCaptureNow() && asrReady && turnActive);
    pcmSender.setEnabled(shouldSend);
  }

  async function ensurePcmSender() {
    if (pcmSender) {
      return pcmSender;
    }
    if (pcmSenderInitPromise) {
      return pcmSenderInitPromise;
    }
    const ws = socket || (WSClient && WSClient._ws) || null;
    if (!ws) {
      throw new Error("WebSocket unavailable for PCM sender");
    }
    pcmSenderInitPromise = initPcmSender(ws, {
      onSampleRate: handleSampleRate,
      onFrame: handlePcmFrame,
      onSend: handlePcmSend,
      onError: handlePcmError,
      chunkMs: PCM_TARGET_BATCH_MS,
      flushIntervalMs: PCM_FLUSH_TIMER_MS,
    }).then((sender) => {
      pcmSender = sender;
      pcmSenderInitPromise = null;
      updatePcmSenderState();
      return sender;
    }).catch((err) => {
      pcmSenderInitPromise = null;
      handlePcmError(err);
      throw err;
    });
    return pcmSenderInitPromise;
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
    if (pcmSender && typeof pcmSender.setEnabled === "function") {
      try {
        pcmSender.setEnabled(false);
      } catch (err) {
        console.warn("pcm.sender.disable_failed", err);
      }
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

  function shouldAutoRearmAfterClosed(reason) {
    if (AppState?.policy?.auto_record_after_greet === false) {
      return false;
    }
    const key = typeof reason === "string" && reason ? reason.trim().toLowerCase() : "";
    if (!key) {
      return true;
    }
    if (key === "end_button") {
      return false;
    }
    return !reasonLooksUserInitiated(key);
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
      micLastChunkAt = Date.now();
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
    const options = opts && typeof opts === "object" ? { ...opts } : {};
    if (!options.recover && typeof pcmRing?.clear === 'function') {
      try { pcmRing.clear(); } catch (err) { try { console.warn("pcmRing.clear failed", err); } catch (_) {} }
    }
    if (!options.recover && primedSessionIds.size) {
      primedSessionIds.clear();
    }
    if (!options.recover) {
      AppState._recoverPrimePending = false;
    }
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
    return WSClient.sendJSON(payload);
  }

  function requestAsrArm(reason) {
    const label = normalizeReason(reason);
    try {
      setAsrArmInFlight(true);
      logStage("client.asr_rearm_request", { reason: label });
      openAsr({ reason: label }); // send first so it's not phase-blocked
      setWsPhase("arming");
    } catch (err) {
      setAsrArmInFlight(false);
      setWsPhase(AppState.wsConnected ? "connected" : "disconnected");
      console.error("Failed to send asr.open", err);
      logStage("client.mic", { outcome: MIC_OUTCOME.ERROR_WS_SEND, message: err?.message });
    }
  }

  async function requestAsrClose(reason = "client_stop") {
    const label = normalizeReason(reason);
    // ASR close is not a transport close; do not touch wsPhase here.
    setAsrArmInFlight(false);
    const seq = (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function")
      ? crypto.randomUUID()
      : `${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const sid = typeof AppState?.asrSid === "string" && AppState.asrSid ? AppState.asrSid : null;
    const payload = { type: "asr.close", seq, reason: label };
    if (sid) {
      payload.sid = sid;
    }
    let ack = null;
    try {
      WSClient.sendJSON(payload);
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
        ack = await WSClient.waitForOnce(
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
    await stopRecorder(label);
    return ack;
  }

  async function recoverFromAsrFault(reason) {
    if (asrRecovering) {
      return;
    }
    asrRecovering = true;
    const label = typeof reason === "string" && reason ? reason : "unknown";
    clearPartialWatchdog();
    try {
      await WSClient.requestAsrClose(`recover:${label}`);
    } catch (err) {
      try { console.warn("ASR recovery close failed", err); } catch (_) {}
    }

    try {
      await WSClient.openAsr({
        vendor: AppState?.asrVendor || DEFAULT_ASR_VENDOR,
        sample_rate: ASR_RATE,
        language: AppState?.language || "en-US",
        recover: true,
      });
    } catch (err) {
      try { console.warn("ASR recovery open failed", err); } catch (_) {}
    }

    try {
      if (typeof WSClient.waitForOnce === 'function') {
        const readyFrame = await WSClient.waitForOnce('asr.ready', () => true, 2000);
        const sid = readyFrame?.sid || AppState?.asrSid || `${Date.now()}`;
        primeAsrStreamFromRing(sid);
        AppState._recoverPrimePending = false;
      } else {
        AppState._recoverPrimePending = true;
      }
    } catch (err) {
      AppState._recoverPrimePending = true;
      try { console.warn("ASR recovery wait_for_ready failed", err); } catch (_) {}
    } finally {
      asrRecovering = false;
    }
  }

  const CLIENT_BANNER_TYPE = "client.banner";
  const CLIENT_BANNER_MAX_HISTORY = 24;
  const CLIENT_BANNER_MAX_QUEUE = 24;
  const CLIENT_BANNER_EVENT_LABEL_MAX = 64;
  const CLIENT_BANNER_STRING_MAX = 240;

  let clientBannerQueue = [];

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

  function cloneQueuedPayload(payload, isBinary = false) {
    if (isBinary) {
      if (payload instanceof ArrayBuffer) {
        try {
          return payload.slice(0);
        } catch (err) {
          console.warn("WSClient queue clone failed (ArrayBuffer)", err);
          return payload;
        }
      }
      if (ArrayBuffer.isView(payload)) {
        try {
          const view = payload;
          return view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength);
        } catch (err) {
          console.warn("WSClient queue clone failed (TypedArray)", err);
          try {
            return payload.slice ? payload.slice(0) : payload;
          } catch {
            return payload;
          }
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
      console.warn("WSClient queue clone failed", err);
      return payload;
    }
  }

  function sendBinary(payload, opts = {}) {
    // Always send PCM when the socket is OPEN. Do not phase-gate audio.
    const ws = WSClient?._ws || window.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      WSClient._queue = WSClient._queue || [];
      const queued = cloneQueuedPayload(payload, true);
      const queueOptions = opts && typeof opts === "object" ? { ...opts } : undefined;
      WSClient._queue.push({ type: "binary", payload, options: queueOptions, data: queued, isBinary: true });
      return false;
    }
    if (payload instanceof Blob) {
      return payload.arrayBuffer().then((buf) => {
        const jsonCandidate = handleBinaryJsonPayload(buf, { source: "wsclient.binary_blob" });
        if (jsonCandidate === false) {
          return false;
        }
        if (jsonCandidate) {
          return send.call(WSClient, jsonCandidate, { binary: false });
        }
        try {
          ws.send(buf);
        } catch (err) {
          console.warn("ws.binary send failed", err);
          throw err;
        }
        return true;
      });
    }
    const jsonCandidate = handleBinaryJsonPayload(payload, { source: "wsclient.binary" });
    if (jsonCandidate === false) {
      return false;
    }
    if (jsonCandidate) {
      return send.call(WSClient, jsonCandidate, { binary: false });
    }
    try {
      ws.send(payload);
    } catch (e) {
      console.warn("ws.binary send failed", e);
      return false;
    }
    return true;
  }

  function sendJson(frame) {
    try {
      if (WSClient && typeof WSClient.sendJSON === "function") {
        return WSClient.sendJSON(frame);
      }
      return send(frame, { binary: false });
    } catch (err) {
      console.error("WSClient sendJson error", err);
      return false;
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

  const ASR_VENDOR_OPTIONS = ['gcp'];
  const AUDIO_PIPELINE_OPTIONS = ['pcm16'];

  const DEFAULT_POLICY_FLAGS = {
    recorder: { stop_on_tts_start: false, mute_send_during_tts: true },
    input: { require_hotword_to_start: false, require_user_gesture_first_visit: false },
    asr: {
      prearm_on_tts_end: false,
      keep_stream_warm_ms: 30000,
      commit_on_vad_silence: true,
      commit_silence_ms: 900,
      max_utterance_ms: 8000,
      vendor: { primary: 'gcp', secondary: null },
    },
    routing: { ws_version: 'v2' },
    audio: { pipeline: { mode: 'pcm16' } },
  };

  function sanitizePolicySnapshot(source) {
    if (!FEATURE_LEGACY_POLICY) {
      const base = (AppState && typeof AppState.policy === 'object') ? AppState.policy : {};
      const sanitized = { ...base };
      const safeSource = source && typeof source === 'object' ? source : {};

      const safeVad = safeSource && typeof safeSource.vad === 'object' ? safeSource.vad : {};
      const baseVad = base && typeof base.vad === 'object' ? base.vad : {};
      sanitized.vad = { ...DEFAULT_POLICY_VAD, ...baseVad, ...safeVad };

      const safeWatchdog = safeSource && typeof safeSource.watchdog === 'object' ? safeSource.watchdog : {};
      const baseWatchdog = base && typeof base.watchdog === 'object' ? base.watchdog : {};
      sanitized.watchdog = { ...DEFAULT_POLICY_WATCHDOG, ...baseWatchdog, ...safeWatchdog };

      const baseUi = base && typeof base.ui === 'object' ? base.ui : {};
      const safeUi = safeSource && typeof safeSource.ui === 'object' ? safeSource.ui : {};
      const baseStatus = baseUi && typeof baseUi.status === 'object' ? baseUi.status : {};
      const safeStatus = safeUi && typeof safeUi.status === 'object' ? safeUi.status : {};
      sanitized.ui = { ...baseUi, ...safeUi };
      sanitized.ui.status = { ...DEFAULT_POLICY_STATUS, ...baseStatus, ...safeStatus };

      Object.keys(safeSource).forEach((key) => {
        if (key === 'vad' || key === 'watchdog' || key === 'ui') {
          return;
        }
        const value = safeSource[key];
        if (typeof value === "undefined") {
          return;
        }
        sanitized[key] = cloneValue(value);
      });

      if (!Number.isFinite(sanitized.version)) {
        sanitized.version = 2;
      }
      if (typeof sanitized._normalized_from !== 'string') {
        sanitized._normalized_from = 'v2';
      }
      // Re-adding deleted autostart policy flags for consistency, though unused in the new flow
      sanitized.autostart_retry_on = sanitized.autostart_retry_on || DEFAULT_POLICY_FLAGS.autostart_retry_on;
      sanitized.autostart_backoff_ms = sanitized.autostart_backoff_ms || DEFAULT_POLICY_FLAGS.autostart_backoff_ms;
      sanitized.autostart_max_attempts = sanitized.autostart_max_attempts || DEFAULT_POLICY_FLAGS.autostart_max_attempts;
      
      return sanitized;
    }

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
        require_hotword_to_start: false,
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
      const vendorDefaults = DEFAULT_POLICY_FLAGS.asr.vendor || { primary: 'gcp', secondary: null };
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

    if (frame.type === "turn.begin") {
      if (AppState?.setState) {
        try {
          AppState.setState({ turnActive: true });
        } catch {}
      }
      updatePcmSenderState();
      try {
        window.dispatchEvent(new CustomEvent("turn.begin", { detail: frame }));
      } catch {}
      return;
    }

    if (frame.type === "turn.end") {
      if (AppState?.setState) {
        try {
          AppState.setState({ turnActive: false });
        } catch {}
      }
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
      dispatchFrame(frame);
      void recoverFromAsrFault("timeout");
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
    } else if (frame.type === "asr.error" || frame.type === "asr.closed" || frame.type === "asr.reset") {
      clearPartialWatchdog();
      __resetAudioHeaderSent();
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
        _audioStreaming = false;
        setListeningState(false);
        resetTurnIntent(frame?.type || "asr.closed");
        emitConsoleBusEvent("client.ui_badge", { state: "Ready" });
      }
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
    } else if (frame.type === "asr.ready") {
      if (typeof frame.sid === "string" && frame.sid) {
        AppState.asrSid = frame.sid;
      }
      frame = handleAsrReadyFrame(frame) || frame;
      // HARD SYNC: Set final state flags
      AppState.asrReady = true;
      setAsrArmInFlight(false);
      setWsConnected(true);
      setWsPhase("ready");
      emitConsoleBusEvent("client.asr.ready", { asrReady: true });

      // *** NEW STABLE LOGIC: Immediately transition to streaming based on server readiness ***
      const startReason = "asr_ready_forced_start";
      hubLog("client.ws_ready_check", {
        socketOpen: !!(WSClient?._ws) && WSClient._ws.readyState === WebSocket.OPEN,
        phase: (AppState?.wsPhase || AppState?.connectionState || null),
      });
      // 1. Open Turn (Idempotent)
      const turned = await openTurnOnce(startReason);
      void turned;
      try {
        // 2. Start Mic Streaming (Triggers mic hardware and sets AppState.listening=true)
        await startRecorderStreaming(frame?.policy || {}, startReason);
        _audioStreaming = true;
      } catch (e) {
        console.warn("auto-arm on asr.ready failed", e);
      }
      // -----------------------------------------------------------------------------------

      try {
        const capturePolicy = AppState?.policy?.capture || {};
        const mode = typeof capturePolicy?.mode === "string" && capturePolicy.mode
          ? capturePolicy.mode
          : "webrtc_aec";
        const ctxRate = window.__audioCtx && typeof window.__audioCtx.sampleRate === "number"
          ? window.__audioCtx.sampleRate
          : 48000;
        emitConsoleBusEvent("client.capture.mode", { mode, ctxSampleRate: ctxRate });
      } catch {}
      logStage("diag", { label: "asr.ready" });
      logStage("client.asr_arm_clear", { vendor: AppState.asrVendor || DEFAULT_ASR_VENDOR });
      // Send the header once. If startRecorderStreaming() already sent it
      // (policy: audio.header_on_first_chunk), this call will no-op.
      sendAudioHeader(frame);
      if (AppState._recoverPrimePending) {
        const sid = frame?.sid || AppState?.asrSid || `${Date.now()}`;
        primeAsrStreamFromRing(sid);
        AppState._recoverPrimePending = false;
      }
    } else if (frame.type === "asr.unavailable") {
      const reason = frame && typeof frame.reason === "string" ? frame.reason : "";
      const details = frame && typeof frame.details === "string"
        ? frame.details
        : (frame && typeof frame.detail === "string" ? frame.detail : "");
      console.warn("asr.unavailable", reason, details);
      AppState.asrReady = false;
      AppState.asrVendor = null;
      updateState({ asrReady: false, asrVendor: null });
      updatePcmSenderState();
      await stopRecorder("asr_unavailable");
      __resetAudioHeaderSent();
      resetTurnIntent(frame?.type || "asr.unavailable");
      setAsrArmInFlight(false);
      if (typeof AppState.emit === "function") {
        AppState.emit("asrReady", { ready: false, reason, vendor: null });
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
    } else if (frame.type === "asr.turn") {
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
        } catch (e) {
          console.warn("safe_mode turn autostart failed", e);
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
      // publish turn state for StatusBar
      try {
        // REMOVED: Nested state update
        if (typeof AppState.setState === "function") AppState.setState({ asrTurnActive: begin });
      } catch {}
      if (!begin) {
        resetTurnIntent(frame?.state || "turn.end");
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
    try {
      const { data } = event;
      if (typeof data === "string") {
        try {
          const frame = JSON.parse(data);
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
    } catch (outerErr) {
      console.error("Uncaught exception in parseFrame", outerErr);
      hubLog("client.ws.parse_crash", { error: outerErr?.message, frame_data: event?.data });
    }
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
              send.call(WSClient, data, { binary: isBinary });
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
      message: (event) => {
        try {
          parseFrame(event);
        } catch (err) {
          console.error("WS message handler critical crash", err);
          hubLog("client.ws.crash", { error: err?.message, source: "onmessage" });
        }
      },
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
      if (pcmSender && typeof pcmSender.setWebSocket === "function") {
        try {
          pcmSender.setWebSocket(null);
        } catch (err) {
          if (typeof console !== "undefined" && typeof console.warn === "function") {
            console.warn("pcm.sender.detach_failed", err);
          }
        }
      }
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
    const originalSend = typeof ws.send === "function" ? ws.send : null;
    if (originalSend) {
      const boundOriginalSend = originalSend.bind(ws);

      const patchedSend = function patchedSend(data, ...rest) {
        const target = (this && typeof this === "object") ? this : ws;
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
              console.warn("WSClient send wrapper: failed to parse string payload", err);
            }
          } else if (isTypedObjectPayload(data)) {
            typedPayload = data;
          }

          if (typedPayload) {
            if (!validateOutboundPayload(typedPayload, { rawPayload: data, source: "ws_instance" })) {
              return undefined;
            }
          }

          if (isTypedObjectPayload(data)) {
            try {
              data = JSON.stringify(data);
            } catch (err) {
              console.warn("WSClient send wrapper: failed to serialize payload", err);
              return undefined;
            }
          }
        }

        try {
          target.__wsClientGuarding = true;
          return boundOriginalSend(data, ...rest);
        } finally {
          try { delete target.__wsClientGuarding; } catch { target.__wsClientGuarding = undefined; }
        }
      };

      ws.send = patchedSend;
      ws.__originalSend = function delegatingOriginalSend(data, ...rest) {
        return patchedSend.call(ws, data, ...rest);
      };
    }
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

      // after ws opens
      try {
        const ws = WSClient._ws || window.ws;
        if (ws && typeof ws.send === 'function') {
          const _send = ws.send.bind(ws);
          ws.send = (data) => {
            try {
              const kind = (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) ? 'binary' : typeof data;
              const size = (data && data.byteLength) || (data && data.size) || null;
              console.log('WS SEND', kind, size);
            } catch (_) {}
            return _send(data);
          };
        }
      } catch (_) {}
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
      const currentTurnState =
        typeof TurnState !== "undefined"
          ? TurnState
          : (typeof window !== "undefined" && window.TurnState) || null;
      if (currentTurnState?.awaitingAssistant) {
        console.warn("WS closed mid-turn", { awaiting: currentTurnState });
      }
      const detailReason = typeof e.reason === "string" && e.reason ? e.reason : "handshake_close";
      recordLastError(typeof e.code === "number" ? e.code : null, detailReason);
      setWsConnected(false);
      setWsPhase("disconnected");
      logStage('client.ws', { outcome: 'close', code: e?.code, reason: e?.reason });
      logMic({ outcome: MIC_OUTCOME.STOPPED, reason: e?.reason || (expected ? 'intentional_close' : 'ws_close') });
      maybeShowHandshakeToast(ws, e && typeof e.code === "number" ? e.code : null);
      recordClientBannerEvent("ws.socket.close", {
        code: typeof e.code === "number" ? e.code : undefined,
        reason: truncateBannerString(e.reason || "", 160),
        was_clean: Boolean(e.wasClean),
        ready_state: ws.readyState,
      });
      // Logic moved to detach/cleanupSocket, kept here for legacy logging/telemetry
    };

    socket = ws;
    if (pcmSender && typeof pcmSender.setWebSocket === "function") {
      try {
        pcmSender.setWebSocket(ws);
      } catch (err) {
        if (typeof console !== "undefined" && typeof console.warn === "function") {
          console.warn("pcm.sender.attach_failed", err);
        }
      }
    }
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
    if (!socket) {
      setWsPhase("disconnected");
      updateState({ connectionState: "disconnected", infoFrame: null, serverBanner: null });
      emitResumeInvalid();
      return;
    }
    const ws = socket;
    cleanupSocket(ws, closeReason);
    clearHeartbeat();
    clearRateLimitRetryTimer();
    rateLimitRetryCount = 0;
    if (window.WSErrorUI && typeof window.WSErrorUI.cancelRateLimitCountdown === "function") {
      try {
        window.WSErrorUI.cancelRateLimitCountdown(closeReason);
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
      const diagnostic = {
        keys: keys.slice(0, 6),
        payload,
        raw: rawPayload,
        source,
        structure,
      };
      console.warn("WSClient send skipped payload with non type-preserving structure", diagnostic);
      try {
        recordClientBannerEvent("ws.send.invalid_payload", {
          reason: "non_type_preserving_structure",
          structure,
          keys: diagnostic.keys,
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
    const diagnostic = {
      keys: keys.slice(0, 6),
      payload,
      raw: rawPayload,
      source,
    };
    console.warn("WSClient send skipped object payload without type", diagnostic);
    try {
      recordClientBannerEvent("ws.send.invalid_payload", {
        reason: "missing_type",
        keys: diagnostic.keys,
        source,
      });
    } catch {}
    try {
      logStage("client.ws", { outcome: "send_skipped_missing_type", keys: diagnostic.keys, source });
    } catch {}
    return false;
  }

  function send(payload, { binary = false, skipPhaseCheck = false } = {}) {
    const client = (this && typeof this === "object") ? this : WSClient;
    if (!Array.isArray(client._queue)) {
      client._queue = [];
    }
    let data = payload;

    if (!binary && (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload))) {
      console.debug("WSClient.send: binary payload ignored by JSON helper");
      return false;
    }

    if (!binary) {
      if (!validateOutboundPayload(data, { source: "wsclient.send" })) {
        return false;
      }
      if (!data || typeof data !== "object") {
        console.warn("WSClient.send blocked: missing or invalid type", payload);
        return false;
      }
      if (typeof data.type !== "string") {
        console.warn("WSClient.send blocked: missing or invalid type", payload);
        return false;
      }
      if (data.type === "audio.header") {
        if (data.format !== "pcm16" ||
            typeof data.sample_rate !== "number" ||
            typeof data.channels !== "number") {
          console.warn("WSClient.send blocked: invalid audio.header schema", payload);
          return false;
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
    let live = client._ws || stateSocket || (WSClient?._ws || window.ws);
    const open = !!live && live.readyState === WebSocket.OPEN;
    const isControl = !binary && isControlFrame(data);

    if (!skipPhaseCheck && !binary && !isControl) {
      try {
        const phase = AppState?.wsPhase || AppState?.connectionState;
        if (!WS_READY_PHASES.has(phase)) {
          const queued = cloneQueuedPayload(data, false);
          client._queue.push({ data: queued, isBinary: false });
          console.warn("WSClient.send queued (phase not ready)", { phase });
          return true;
        }
      } catch {}
    }

    if (!open) {
      const queued = cloneQueuedPayload(data, !!binary);
      client._queue.push({ data: queued, isBinary: !!binary });
      console.warn("WSClient.send queued (socket not open)");
      return true;
    }

    client._ws = live;
    client._connected = true;
    try { live.binaryType = "arraybuffer"; } catch {}

    if (!binary && isControl) {
      try {
        live.send(typeof data === "string" ? data : JSON.stringify(data));
        return true;
      } catch (err) {
        console.error("WSClient send error", err);
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
            console.error("WSClient binary send error", err);
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
          console.error("WSClient binary send error", err);
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
      console.error("WSClient send error", err);
      return false;
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
    setTransportFactory(factory) {
      transportFactory = typeof factory === "function" ? factory : transportFactory;
    }
  };

  WSClient.sendJSON = function sendJSONPayload(obj) {
    if (obj instanceof ArrayBuffer || ArrayBuffer.isView(obj)) {
      logTransportMisuse("binary_sent_to_sendJSON");
      console.error("WSClient.sendJSON: binary payload; use sendAudioChunk()");
      return false;
    }
    if (!obj || typeof obj !== "object") {
      return false;
    }
    const result = send.call(WSClient, obj, { binary: false });
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
      const blobResult = sendBinary(buf, { ...opts, lane: opts && typeof opts === "object" && typeof opts.lane !== "undefined" ? opts.lane : "mic" });
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
    const result = sendBinary(buf, options);
    if (result && typeof result.then === "function") {
      return result;
    }
    return result !== false;
  };

  WSClient.open = open;
  WSClient.close = close;
  WSClient.send = function sendJSON(payload) {
    try {
      const ws = WSClient?._ws || window.ws;
      const open = ws && ws.readyState === WebSocket.OPEN;
      if (open && isControlFrame(payload)) {
        try {
          ws.send(JSON.stringify(payload));
          return true;
        } catch (e) {
          console.warn("ws.json send failed", e);
          return false;
        }
      }
    } catch {}
    return WSClient.sendJSON(payload);
  };

  // Fast-path for callers that use sendJSON() directly (audio.header, pings, etc.).
  (function wrapSendJSON() {
    const __origSendJSON = WSClient.sendJSON;
    WSClient.sendJSON = function sendJSONFast(frame) {
      try {
        const ws = WSClient?._ws || window.ws;
        const open = ws && ws.readyState === WebSocket.OPEN;
        const isControl = isControlFrame(frame);
        if (open && isControl) {
          try {
            ws.send(JSON.stringify(frame));
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
  WSClient.sendBinary = (payload, opts = {}) => sendBinary(payload, opts);
  WSClient.getBufferedAmount = getBufferedAmount;
  WSClient.requestAsrArm = requestAsrArm;
  WSClient.openAsr = openAsr;
  WSClient.requestAsrClose = requestAsrClose;
  WSClient.recoverFromAsrFault = recoverFromAsrFault;
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
  WSClient._connected = !!(WSClient._ws && WSClient._ws.readyState === WebSocket.OPEN);
  WSClient._queue = Array.isArray(WSClient._queue) ? WSClient._queue : [];
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
