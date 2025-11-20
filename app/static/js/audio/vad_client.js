/* eslint-disable no-console */
import { logStage } from "../ws/telemetry.js";
const DB_FLOOR = -120;
const SAMPLE_MAX = 32768;
const STATE_PUBLISH_INTERVAL_MS = 500;
const GATE_HEARTBEAT_INTERVAL_MS = 500;
const NOISE_FALL_ALPHA = 0.2;
const NOISE_RISE_ALPHA = 0.05;

const DEFAULTS = {
  noiseDb: -72,
  thresholdDb: 8,
  ttsThresholdBoostDb: 6,
  minSpeechMs: 120,
  minSilenceMs: 240,
  holdMs: 200,
};

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function toInt16Frame(buffer) {
  if (!buffer) {
    return new Int16Array(0);
  }
  if (buffer instanceof Int16Array) {
    return buffer;
  }
  if (ArrayBuffer.isView(buffer) && buffer.BYTES_PER_ELEMENT === 2) {
    try {
      return new Int16Array(buffer.buffer, buffer.byteOffset, buffer.length);
    } catch {
      return new Int16Array(0);
    }
  }
  if (buffer instanceof ArrayBuffer) {
    return new Int16Array(buffer);
  }
  return new Int16Array(0);
}

function rmsDbFromPcm(int16Frame) {
  const { length } = int16Frame;
  if (!length) {
    return DB_FLOOR;
  }
  let sumSquares = 0;
  for (let i = 0; i < length; i += 1) {
    const sample = int16Frame[i] / SAMPLE_MAX;
    sumSquares += sample * sample;
  }
  const meanSquare = sumSquares / length;
  if (meanSquare <= 0) {
    return DB_FLOOR;
  }
  const rms = Math.sqrt(meanSquare);
  return 20 * Math.log10(rms);
}

function lerp(current, target, alpha) {
  return current + (target - current) * alpha;
}

function resolvePolicy(getPolicy) {
  if (typeof getPolicy !== "function") {
    return null;
  }
  try {
    return getPolicy() || null;
  } catch {
    return null;
  }
}

function resolveStreamGateMode(getPolicy) {
  const policy = resolvePolicy(getPolicy);
  if (!policy || typeof policy !== "object") {
    return "pass";
  }
  const vadBlock = policy.vad && typeof policy.vad === "object" ? policy.vad : null;
  if (!vadBlock) {
    return "pass";
  }
  const clientBlock = vadBlock.client && typeof vadBlock.client === "object"
    ? vadBlock.client
    : null;
  if (!clientBlock) {
    return "pass";
  }
  const mode = clientBlock.stream_gate;
  if (mode === "gate") {
    return "gate";
  }
  return "pass";
}

function isTtsActive(getTtsActive) {
  if (typeof getTtsActive !== "function") {
    return false;
  }
  try {
    return !!getTtsActive();
  } catch {
    return false;
  }
}

function safePublish(publish, event, payload) {
  if (typeof publish !== "function") {
    return;
  }
  try {
    publish(event, payload);
  } catch (err) {
    if (typeof console !== "undefined" && typeof console.warn === "function") {
      console.warn("client.vad publish error", err);
    }
  }
}

function safeSetAppState(setAppState, patch) {
  if (typeof setAppState !== "function" || !patch || typeof patch !== "object") {
    return;
  }
  try {
    setAppState(patch);
  } catch (err) {
    if (typeof console !== "undefined" && typeof console.warn === "function") {
      console.warn("client.vad setAppState error", err);
    }
  }
}

export function initVAD({
  getPolicy,
  getTtsActive,
  onGateChange,
  setAppState,
  publish,
} = {}) {
  let vadSpeech = false;
  let vadEnergyDb = DB_FLOOR;
  let vadNoiseDb = DEFAULTS.noiseDb;
  let speechCandidateStart = null;
  let silenceCandidateStart = null;
  let speechStartedAt = null;
  let holdUntilMs = 0;
  let lastStatePublishAt = 0;
  let gatePaused = false;
  let gatePausedAt = 0;
  let lastGateHeartbeatAt = 0;
  let initialized = false;

  function computeThresholdDb() {
    const base = DEFAULTS.thresholdDb;
    const ttsActive = isTtsActive(getTtsActive);
    const boost = ttsActive ? DEFAULTS.ttsThresholdBoostDb : 0;
    const effectiveNoise = Number.isFinite(vadNoiseDb) ? vadNoiseDb : DEFAULTS.noiseDb;
    const threshold = effectiveNoise + base + boost;
    return Math.max(threshold, DEFAULTS.noiseDb + base);
  }

  function updateGate(nowMs, reason) {
    const mode = resolveStreamGateMode(getPolicy);
    if (mode !== "gate") {
      if (gatePaused && typeof onGateChange === "function") {
        gatePaused = false;
        gatePausedAt = 0;
        lastGateHeartbeatAt = 0;
        onGateChange("resume", "policy_disabled");
        safePublish(publish, "client.vad.gate", { action: "resume", reason: "policy_disabled" });
      }
      return;
    }

    if (vadSpeech) {
      if (gatePaused && typeof onGateChange === "function") {
        gatePaused = false;
        gatePausedAt = 0;
        lastGateHeartbeatAt = 0;
        onGateChange("resume", reason || "speech_detected");
        safePublish(publish, "client.vad.gate", { action: "resume", reason: reason || "speech_detected" });
      }
    } else {
      if (!gatePaused && nowMs >= holdUntilMs && typeof onGateChange === "function") {
        gatePaused = true;
        gatePausedAt = nowMs;
        lastGateHeartbeatAt = nowMs;
        onGateChange("pause", reason || "silence");
        safePublish(publish, "client.vad.gate", { action: "pause", reason: reason || "silence" });
      }
      if (gatePaused) {
        if (!lastGateHeartbeatAt) {
          lastGateHeartbeatAt = nowMs;
        }
        if (nowMs - lastGateHeartbeatAt >= GATE_HEARTBEAT_INTERVAL_MS) {
          lastGateHeartbeatAt = nowMs;
          safePublish(publish, "client.vad.gate_heartbeat", {
            paused: true,
            ms_since_pause: nowMs - gatePausedAt,
          });
        }
      }
    }
  }

  function emitState(nowMs, force = false) {
    if (!force && nowMs - lastStatePublishAt < STATE_PUBLISH_INTERVAL_MS) {
      return;
    }
    lastStatePublishAt = nowMs;
    safePublish(publish, "client.vad.state", {
      speech: vadSpeech,
      conf: computeConfidence(nowMs),
      energyDb: vadEnergyDb,
      noiseDb: vadNoiseDb,
    });
  }

  function computeConfidence(nowMs) {
    const thresholdDb = computeThresholdDb();
    const diff = vadEnergyDb - thresholdDb;
    const normalized = (diff + 12) / 24;
    return clamp(Number.isFinite(normalized) ? normalized : 0, 0, 1);
  }

  function updateAppState(nowMs) {
    safeSetAppState(setAppState, {
      vadActive: true,
      vadSpeech,
      vadConfidence: computeConfidence(nowMs),
      vadEnergyDb,
      vadNoiseDb,
    });
  }

  function resetState(nowMs) {
    vadSpeech = false;
    vadEnergyDb = DB_FLOOR;
    vadNoiseDb = DEFAULTS.noiseDb;
    speechCandidateStart = null;
    silenceCandidateStart = null;
    speechStartedAt = null;
    holdUntilMs = 0;
    initialized = false;
    if (gatePaused && typeof onGateChange === "function") {
      onGateChange("resume", "reset");
      safePublish(publish, "client.vad.gate", { action: "resume", reason: "reset" });
    }
    gatePaused = false;
    gatePausedAt = 0;
    lastGateHeartbeatAt = 0;
    lastStatePublishAt = 0;
    updateAppState(nowMs || Date.now());
    emitState(nowMs || Date.now(), true);
  }

  function onPcmFrame(buffer, timestampMs) {
    try {
      logStage("vad.frame_input", {
        timestampMs: typeof timestampMs === "number" ? timestampMs : null,
        length: Array.isArray(buffer) || buffer instanceof Int16Array ? buffer.length : 0,
      });
    } catch (_) {}
    const nowMs = Number.isFinite(timestampMs) ? timestampMs : Date.now();
    const frame = toInt16Frame(buffer);
    vadEnergyDb = rmsDbFromPcm(frame);
    if (!initialized) {
      vadNoiseDb = Number.isFinite(vadEnergyDb) ? Math.min(vadEnergyDb, DEFAULTS.noiseDb) : DEFAULTS.noiseDb;
      initialized = true;
    }
    if (!Number.isFinite(vadNoiseDb)) {
      vadNoiseDb = DEFAULTS.noiseDb;
    }

    const thresholdDb = computeThresholdDb();
    const aboveThreshold = vadEnergyDb > thresholdDb;

    if (aboveThreshold) {
      vadNoiseDb = lerp(vadNoiseDb, vadEnergyDb - DEFAULTS.thresholdDb, NOISE_RISE_ALPHA);
    } else {
      vadNoiseDb = lerp(vadNoiseDb, vadEnergyDb, NOISE_FALL_ALPHA);
    }
    vadNoiseDb = clamp(vadNoiseDb, DB_FLOOR, vadEnergyDb);

    if (aboveThreshold) {
      silenceCandidateStart = null;
      if (speechCandidateStart === null) {
        speechCandidateStart = nowMs;
      }
      if (!vadSpeech && nowMs - speechCandidateStart >= DEFAULTS.minSpeechMs) {
        vadSpeech = true;
        speechStartedAt = speechCandidateStart;
        safePublish(publish, "client.vad.speech_start", {
          conf: computeConfidence(nowMs),
          energyDb: vadEnergyDb,
          noiseDb: vadNoiseDb,
        });
        emitState(nowMs, true);
      }
    } else {
      speechCandidateStart = null;
      if (vadSpeech && silenceCandidateStart === null) {
        silenceCandidateStart = nowMs;
      }
      if (vadSpeech && silenceCandidateStart !== null && nowMs - silenceCandidateStart >= DEFAULTS.minSilenceMs) {
        vadSpeech = false;
        holdUntilMs = nowMs + DEFAULTS.holdMs;
        const durationMs = speechStartedAt ? Math.max(0, nowMs - speechStartedAt) : 0;
        safePublish(publish, "client.vad.speech_end", { duration_ms: durationMs });
        emitState(nowMs, true);
        speechStartedAt = null;
      }
    }

    updateAppState(nowMs);
    emitState(nowMs);
    updateGate(nowMs);
  }

  const bootTs = Date.now();
  updateAppState(bootTs);
  emitState(bootTs, true);

  return {
    onPcmFrame,
    reset() {
      resetState(Date.now());
    },
  };
}
