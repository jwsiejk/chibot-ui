// app/static/js/ws/policy_runtime.js
// Encapsulates client-side policy merging and access helpers for ws_client.js.

const MAX_GATE_SILENCE_MS = 3000;

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
  stream_gate: "gate", // keep gating if you want to save bandwidth
  max_gate_silence_ms: MAX_GATE_SILENCE_MS,
  // Optional: per-deployment configurable warmup in ms
  warmup_ms: 1200,
  // If your input is very quiet, allow threshold override via policy
});

const CLIENT_VAD_POLICY_ROOT = Object.freeze({ vad: Object.freeze({ client: CLIENT_VAD_POLICY }) });
const DEFAULT_POLICY_VAD = { warmup_ms: 1200, sender_gate_on_tts: true };
const DEFAULT_POLICY_WATCHDOG = {
  partial_wait_ms_first_turn: 3500,
  partial_wait_ms: 2500,
};
const DEFAULT_POLICY_STATUS = { require_active_turn: true };
const DEFAULT_POLICY_FLAGS = {
  recorder: { stop_on_tts_start: false, mute_send_during_tts: true },
  input: { require_hotword_to_start: false, require_user_gesture_first_visit: false },
  asr: {
    prearm_on_tts_end: false,
    keep_stream_warm_ms: 30000,
    commit_on_vad_silence: true,
    commit_silence_ms: 900,
    max_utterance_ms: 8000,
    vendor: { primary: "gcp", secondary: null },
  },
  routing: { ws_version: "v2" },
  audio: { pipeline: { mode: "pcm16" }, keepalive_ms: 1000 },
};
const ASR_VENDOR_OPTIONS = ["gcp"];
const AUDIO_PIPELINE_OPTIONS = ["pcm16"];

const FEATURE_LEGACY_POLICY = Boolean(
  (typeof window !== "undefined" && window.FEATURE_LEGACY_POLICY) ?? false,
);

const cloneValue = (value) => {
  if (Array.isArray(value)) {
    return value.slice();
  }
  if (value && typeof value === "object") {
    return { ...value };
  }
  return value;
};

export function createPolicyRuntime(AppState, options = {}) {
  const {
    updateState: updateStateOption,
    dispatchFrame: dispatchFrameOption,
    reasonLooksUserInitiated: reasonLooksUserInitiatedOption,
  } = options || {};

  const updateState = typeof updateStateOption === "function"
    ? updateStateOption
    : (typeof AppState?.setState === "function"
      ? (patch) => AppState.setState(patch)
      : () => {});

  const dispatchFrame = typeof dispatchFrameOption === "function" ? dispatchFrameOption : () => {};
  const reasonLooksUserInitiated = typeof reasonLooksUserInitiatedOption === "function"
    ? reasonLooksUserInitiatedOption
    : () => false;

  let currentPolicy = (AppState && typeof AppState.policy === "object") ? AppState.policy : {};
  let clientVadPolicyRoot = CLIENT_VAD_POLICY_ROOT;

  function computeClientVadPolicyRootFromSource(source) {
    try {
      const root = source && typeof source === "object" ? source : currentPolicy;
      const client = root?.vad?.client;
      if (client && typeof client === "object") {
        return { vad: { client: { ...CLIENT_VAD_POLICY, ...client } } };
      }
    } catch (err) {
      // swallow
    }
    return CLIENT_VAD_POLICY_ROOT;
  }

  function installClientVadPolicySnapshot(snapshot) {
    const policyRoot = snapshot && typeof snapshot === "object"
      ? snapshot
      : (AppState.policy && typeof AppState.policy === "object"
        ? AppState.policy
        : (AppState.policy = {}));

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

    if (!snapshot) {
      currentPolicy = (AppState && typeof AppState.policy === "object") ? AppState.policy : policyRoot;
    }
    clientVadPolicyRoot = computeClientVadPolicyRootFromSource(policyRoot);
    return policyRoot;
  }

  function sanitizePolicySnapshot(source) {
    if (!FEATURE_LEGACY_POLICY) {
      const base = (AppState && typeof AppState.policy === "object") ? AppState.policy : {};
      const sanitized = { ...base };
      const safeSource = source && typeof source === "object" ? source : {};

      const safeVad = safeSource && typeof safeSource.vad === "object" ? safeSource.vad : {};
      const baseVad = base && typeof base.vad === "object" ? base.vad : {};
      sanitized.vad = { ...DEFAULT_POLICY_VAD, ...baseVad, ...safeVad };

      const safeWatchdog = safeSource && typeof safeSource.watchdog === "object" ? safeSource.watchdog : {};
      const baseWatchdog = base && typeof base.watchdog === "object" ? base.watchdog : {};
      sanitized.watchdog = { ...DEFAULT_POLICY_WATCHDOG, ...baseWatchdog, ...safeWatchdog };

      const baseUi = base && typeof base.ui === "object" ? base.ui : {};
      const safeUi = safeSource && typeof safeSource.ui === "object" ? safeSource.ui : {};
      const baseStatus = baseUi && typeof baseUi.status === "object" ? baseUi.status : {};
      const safeStatus = safeUi && typeof safeUi.status === "object" ? safeUi.status : {};
      sanitized.ui = { ...baseUi, ...safeUi };
      sanitized.ui.status = { ...DEFAULT_POLICY_STATUS, ...baseStatus, ...safeStatus };

      Object.keys(safeSource).forEach((key) => {
        if (key === "vad" || key === "watchdog" || key === "ui") {
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
      if (typeof sanitized._normalized_from !== "string") {
        sanitized._normalized_from = "v2";
      }
      // Re-adding deleted autostart policy flags for consistency, though unused in the new flow
      sanitized.autostart_retry_on = sanitized.autostart_retry_on || DEFAULT_POLICY_FLAGS.autostart_retry_on;
      sanitized.autostart_backoff_ms = sanitized.autostart_backoff_ms || DEFAULT_POLICY_FLAGS.autostart_backoff_ms;
      sanitized.autostart_max_attempts = sanitized.autostart_max_attempts || DEFAULT_POLICY_FLAGS.autostart_max_attempts;

      return sanitized;
    }

    const base = (AppState && typeof AppState.policy === "object") ? AppState.policy : {};
    const policy = { ...base };

    if (source && typeof source === "object") {
      if (typeof source.mode === "string") {
        policy.mode = source.mode;
      }
      if (typeof source.allow_auto_vad === "boolean") {
        policy.allow_auto_vad = source.allow_auto_vad;
      }
      if (typeof source.barge_in_enabled === "boolean") {
        policy.barge_in_enabled = source.barge_in_enabled;
      }
      if (typeof source.ws_auth_mode === "string" && source.ws_auth_mode.trim()) {
        policy.ws_auth_mode = source.ws_auth_mode.trim();
      }
      if (typeof source.require_user_gesture_first_visit === "boolean") {
        policy.require_user_gesture_first_visit = source.require_user_gesture_first_visit;
      }
      if (typeof source.auto_record_after_greet === "boolean") {
        policy.auto_record_after_greet = source.auto_record_after_greet;
      }
      if (typeof source.tts_gate_enabled === "boolean") {
        policy.tts_gate_enabled = source.tts_gate_enabled;
      }
      if (Array.isArray(source.autostart_retry_on)) {
        policy.autostart_retry_on = source.autostart_retry_on
          .filter((item) => typeof item === "string" && item)
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
      if (source.capture && typeof source.capture === "object") {
        policy.capture = { ...source.capture };
      }
      if (source.media && typeof source.media === "object") {
        policy.media = { ...source.media };
      }
      if (source.voice && typeof source.voice === "object") {
        policy.voice = { ...source.voice };
      }
      if (source.greet && typeof source.greet === "object") {
        policy.greet = { ...source.greet };
      }
      if (source.suggestions && typeof source.suggestions === "object") {
        policy.suggestions = { ...source.suggestions };
      }
      if (source.actions && typeof source.actions === "object") {
        policy.actions = { ...source.actions };
      }
      if (source.telemetry && typeof source.telemetry === "object") {
        policy.telemetry = { ...source.telemetry };
      }
    }

    const existingNested = (policy && typeof policy.policy === "object") ? policy.policy : {};
    const nested = {
      recorder: {
        ...DEFAULT_POLICY_FLAGS.recorder,
        ...(existingNested && typeof existingNested.recorder === "object" ? existingNested.recorder : {}),
      },
      input: {
        ...DEFAULT_POLICY_FLAGS.input,
        ...(existingNested && typeof existingNested.input === "object" ? existingNested.input : {}),
      },
      asr: {
        ...DEFAULT_POLICY_FLAGS.asr,
        ...(existingNested && typeof existingNested.asr === "object" ? existingNested.asr : {}),
      },
      routing: {
        ...DEFAULT_POLICY_FLAGS.routing,
        ...(existingNested && typeof existingNested.routing === "object" ? existingNested.routing : {}),
      },
    };

    const rawNested = policy && typeof policy.policy === "object" ? policy.policy : {};
    const recorder = rawNested.recorder && typeof rawNested.recorder === "object"
      ? rawNested.recorder
      : null;
    nested.recorder = {
      stop_on_tts_start: recorder && typeof recorder.stop_on_tts_start === "boolean"
        ? recorder.stop_on_tts_start
        : DEFAULT_POLICY_FLAGS.recorder.stop_on_tts_start,
      mute_send_during_tts: recorder && typeof recorder.mute_send_during_tts === "boolean"
        ? recorder.mute_send_during_tts
        : DEFAULT_POLICY_FLAGS.recorder.mute_send_during_tts,
    };

    const input = rawNested.input && typeof rawNested.input === "object"
      ? rawNested.input
      : null;
    nested.input = {
      require_hotword_to_start: false,
      require_user_gesture_first_visit: input && typeof input.require_user_gesture_first_visit === "boolean"
        ? input.require_user_gesture_first_visit
        : DEFAULT_POLICY_FLAGS.input.require_user_gesture_first_visit,
    };

    const asr = rawNested.asr && typeof rawNested.asr === "object" ? rawNested.asr : null;
    let keepWarm = DEFAULT_POLICY_FLAGS.asr.keep_stream_warm_ms;
    if (asr && Number.isFinite(Number(asr.keep_stream_warm_ms))) {
      const parsed = Number(asr.keep_stream_warm_ms);
      if (parsed >= 0) {
        keepWarm = Math.round(parsed);
      }
    }
    const commitOnVad = asr && typeof asr.commit_on_vad_silence === "boolean"
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
    const vendorDefaults = DEFAULT_POLICY_FLAGS.asr.vendor || { primary: "gcp", secondary: null };
    const vendorBlock = asr && typeof asr.vendor === "object" ? asr.vendor : null;
    const vendor = { ...vendorDefaults };
    if (vendorBlock) {
      if (typeof vendorBlock.primary === "string") {
        const normalized = vendorBlock.primary.trim().toLowerCase();
        if (ASR_VENDOR_OPTIONS.includes(normalized)) {
          vendor.primary = normalized;
        }
      }
      if (vendorBlock.secondary === null) {
        vendor.secondary = null;
      } else if (typeof vendorBlock.secondary === "string") {
        const normalizedSecondary = vendorBlock.secondary.trim().toLowerCase();
        if (ASR_VENDOR_OPTIONS.includes(normalizedSecondary)) {
          vendor.secondary = normalizedSecondary;
        } else {
          vendor.secondary = vendorDefaults.secondary;
        }
      }
    }

    nested.asr = {
      prearm_on_tts_end: asr && typeof asr.prearm_on_tts_end === "boolean"
        ? asr.prearm_on_tts_end
        : DEFAULT_POLICY_FLAGS.asr.prearm_on_tts_end,
      keep_stream_warm_ms: keepWarm,
      commit_on_vad_silence: commitOnVad,
      commit_silence_ms: commitSilence,
      max_utterance_ms: maxUtterance,
      vendor,
    };

    const routing = rawNested.routing && typeof rawNested.routing === "object"
      ? rawNested.routing
      : null;
    const rawVersion = routing && typeof routing.ws_version === "string"
      ? routing.ws_version.trim()
      : "";
    nested.routing = {
      ws_version: rawVersion && rawVersion.toLowerCase() === "v2"
        ? "v2"
        : DEFAULT_POLICY_FLAGS.routing.ws_version,
    };

    const audioSource = source && typeof source === "object" ? source.audio : null;
    const audioDefaults = DEFAULT_POLICY_FLAGS.audio || { pipeline: { mode: "pcm16" } };
    const audioPipeline = audioDefaults.pipeline ? { ...audioDefaults.pipeline } : { mode: "pcm16" };
    let keepaliveMs = audioDefaults.keepalive_ms;
    if (audioSource && typeof audioSource === "object") {
      const pipeline = audioSource.pipeline && typeof audioSource.pipeline === "object"
        ? audioSource.pipeline
        : null;
      if (pipeline && typeof pipeline.mode === "string") {
        const mode = pipeline.mode.trim().toLowerCase();
        if (AUDIO_PIPELINE_OPTIONS.includes(mode)) {
          audioPipeline.mode = mode;
        }
      }
      if (typeof audioSource.keepalive_ms === "number" && audioSource.keepalive_ms > 0) {
        keepaliveMs = audioSource.keepalive_ms;
      }
    }

    policy.policy = nested;
    policy.input = nested && typeof nested.input === "object" ? { ...nested.input } : {};
    policy.audio = { pipeline: audioPipeline, keepalive_ms: keepaliveMs };
    return policy;
  }

  function sanitizePolicyFrame(frame) {
    const safe = { type: "policy.interaction" };
    if (frame && typeof frame === "object") {
      Object.keys(frame).forEach((key) => {
        if (key === "policy") return;
        safe[key] = frame[key];
      });
    }
    const source = frame && typeof frame === "object" ? frame.policy : null;
    safe.policy = sanitizePolicySnapshot(source);
    return safe;
  }

  function applyPolicySnapshotFromSource(source, origin) {
    const sanitizedPolicy = sanitizePolicySnapshot(source);
    AppState.policy = sanitizedPolicy;
    currentPolicy = sanitizedPolicy;
    clientVadPolicyRoot = computeClientVadPolicyRootFromSource(sanitizedPolicy);
    updateState({ policy: sanitizedPolicy });
    const snapshotFrame = { type: "policy.snapshot", policy: sanitizedPolicy, origin: origin || null };
    dispatchFrame(snapshotFrame);
    dispatchFrame({ type: "config.updated", policy: sanitizedPolicy, origin: origin || null });
    return sanitizedPolicy;
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

  function getCurrentPolicy() {
    return currentPolicy || {};
  }

  function getClientVadPolicyRoot() {
    return clientVadPolicyRoot || CLIENT_VAD_POLICY_ROOT;
  }

  function initializePolicyState() {
    const P0 = AppState && typeof AppState.policy === "object" ? AppState.policy : {};
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

    installClientVadPolicySnapshot();

    currentPolicy = AppState && typeof AppState.policy === "object" ? AppState.policy : {};
    clientVadPolicyRoot = computeClientVadPolicyRootFromSource(currentPolicy);
  }

  initializePolicyState();

  return {
    getCurrentPolicy,
    applyPolicySnapshotFromSource,
    installClientVadPolicySnapshot,
    shouldAutoRearmAfterClosed,
    getClientVadPolicyRoot,
    sanitizePolicySnapshot,
    sanitizePolicyFrame,
  };
}
