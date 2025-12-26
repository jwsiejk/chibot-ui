// app/static/js/audio/ws_audio_runtime.js
// Encapsulates PCM ring buffer, PCM sender, and ASR priming helpers.

import { isTypedArray, toArrayBuffer } from "../utils/binary.js";
import { recordClientBannerEvent } from "../ws/telemetry.js";
import { logStage } from "../ws/telemetry.js";
import { getMicAudioContext } from "./audio_core.js";

function wsDiag(tag, detail = {}) {
  try {
    console.debug("[WS-DIAG]", tag, detail);
    if (typeof window !== "undefined" && window.emitClientLog) {
      window.emitClientLog("ws_diag", { tag, ...detail });
    }
  } catch (_) {}
}

const PCM_TARGET_SAMPLE_RATE = 16000;
const DEFAULT_RING_CAPACITY_MS = 1500;
const DEFAULT_PRE_SPEECH_BUFFER_MS = 800;
const DEFAULT_PCM_CHANNELS = 1;
const PCM_TARGET_BATCH_MS = 60;
const PCM_FLUSH_TIMER_MS = 50;
const AUDIO_KEEPALIVE_MS = 1000;
const AUDIO_KEEPALIVE_CHUNK_MS = 20;
const AUDIO_KEEPALIVE_IDLE_MS = 30000;
let audioKeepaliveMs = AUDIO_KEEPALIVE_MS;
let audioKeepaliveIdleMs = AUDIO_KEEPALIVE_IDLE_MS;
const PCM_SENDER_DEBUG = false;
let __firstPcmFrameLogged = false;
const WS_READY_PHASES = new Set(["connected", "ready"]);
let wsAudioDropCount = 0;
const WS_AUDIO_DROP_LOG_LIMIT = 5;

const primedSessionIds = new Set();

let pcmWarm = false;
function markPcmWarm() {
  pcmWarm = true;
  try { logStage("client.pcm.warm"); } catch (_) {}
}

function resolveAppState(provided) {
  if (provided) return provided;
  if (typeof window !== "undefined") {
    return window.AppState;
  }
  return undefined;
}

function logPcmEnergy(buffer, meta) {
  try {
    const arrayBuffer = toArrayBuffer(buffer);
    if (!arrayBuffer) return;
    const view = new Int16Array(arrayBuffer);
    const n = view.length || 1;
    if (!n) return;
    let sumSq = 0;
    let peak = 0;
    for (let i = 0; i < n; i++) {
      const v = view[i] / 32768.0;
      sumSq += v * v;
      const abs = Math.abs(v);
      if (abs > peak) peak = abs;
    }
    const rms = Math.sqrt(sumSq / n);
    const dbfs = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
    const appState = resolveAppState();
    const appSnapshot = appState?.get?.() || appState?.getState?.() || appState || null;
    const vadEnergyDb = Number.isFinite(appSnapshot?.vadEnergyDb) ? appSnapshot.vadEnergyDb : null;
    const autoVadEnergyDbfs = Number.isFinite(meta?.auto_vad_energy)
      ? meta.auto_vad_energy
      : Number.isFinite(meta?.autoVadEnergyDbfs)
        ? meta.autoVadEnergyDbfs
        : Number.isFinite(appSnapshot?.autoVadEnergyDbfs)
          ? appSnapshot.autoVadEnergyDbfs
          : null;
    logStage("client.pcm.energy", {
      lane: meta?.lane || "mic",
      reqId: meta?.reqId || null,
      dbfs,
      dbfs_str: Number.isFinite(dbfs) ? dbfs.toFixed(1) : null,
      peak,
      vadEnergyDb,
      autoVadEnergyDbfs,
    });
  } catch (_) {}
}

class PcmRingBuffer {
  constructor({ millis, sampleRate, channels = DEFAULT_PCM_CHANNELS }) {
    this.sampleRate = sampleRate;
    this.channels = channels;
    this.maxSamples = Math.ceil((millis / 1000) * sampleRate) * channels;
    this.buf = new Int16Array(this.maxSamples);
    this.write = 0;
    this.filled = false;
    this.overflowCount = 0;
    this.lastOverflowed = false;
  }

  push(int16Chunk) {
    const data = int16Chunk;
    const n = data.length;
    if (n >= this.maxSamples) {
      this.buf.set(data.subarray(n - this.maxSamples));
      this.write = 0;
      this.filled = true;
      this.overflowCount += 1;
      this.lastOverflowed = true;
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
    this.lastOverflowed = this.filled || end > this.maxSamples;
    if (this.lastOverflowed) {
      this.overflowCount += 1;
    }
    this.write = (end % this.maxSamples);
    if (this.write === 0) this.filled = true;
  }

  drainAll() {
    const size = this.filled ? this.maxSamples : this.write;
    if (!size) {
      return [];
    }
    const out = new Int16Array(size);
    const start = (this.write - size + this.maxSamples) % this.maxSamples;
    if (start + size <= this.maxSamples) {
      out.set(this.buf.subarray(start, start + size), 0);
    } else {
      const first = this.maxSamples - start;
      out.set(this.buf.subarray(start), 0);
      out.set(this.buf.subarray(0, size - first), first);
    }
    this.clear();
    return [out];
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

  clear() {
    this.write = 0;
    this.filled = false;
  }
}

function createRingBufferManager(sampleRate) {
  let ring = null;
  let preSpeechBufferMs = DEFAULT_PRE_SPEECH_BUFFER_MS;
  let capacityMs = DEFAULT_RING_CAPACITY_MS;
  let lastStatus = null;
  let lastStatusAt = 0;
  const statusIntervalMs = 15000;
  let lastOverflowed = false;

  function logStatus(reason = "status", { draining = false, force = false } = {}) {
    if (!ring) return;
    const now = Date.now();
    if (!force && now - lastStatusAt < statusIntervalMs) {
      return;
    }
    const framesStored = ring.filled ? ring.maxSamples : ring.write;
    const statusSignature = `${reason}:${framesStored}:${draining ? "drain" : "idle"}`;
    if (statusSignature === lastStatus) return;
    lastStatus = statusSignature;
    lastStatusAt = now;
    try {
      logStage("client.deepgram_v3.ring_buffer_status", {
        preSpeechBufferMs,
        capacityMs,
        sampleRate: ring.sampleRate,
        framesStored,
        samplesStored: framesStored,
        bytesStored: framesStored * Int16Array.BYTES_PER_ELEMENT,
        filled: ring.filled,
        overflowed: ring.lastOverflowed,
        overflowCount: ring.overflowCount,
        draining,
        reason,
      });
    } catch (_) {}
  }

  function init(ms, sr) {
    preSpeechBufferMs = Number.isFinite(ms) && ms > 0 ? ms : DEFAULT_PRE_SPEECH_BUFFER_MS;
    capacityMs = Math.max(DEFAULT_RING_CAPACITY_MS, preSpeechBufferMs);
    const rate = Number.isFinite(sr) && sr > 0 ? sr : PCM_TARGET_SAMPLE_RATE;
    ring = new PcmRingBuffer({ millis: capacityMs, sampleRate: rate, channels: DEFAULT_PCM_CHANNELS });
    lastOverflowed = false;
    logStatus("init", { force: true });
    return ring;
  }

  function pushFrame(frame) {
    if (!ring) return;
    try {
      ring.push(frame);
      if (ring.lastOverflowed && !lastOverflowed) {
        logStatus("overflow", { force: true });
      }
      lastOverflowed = ring.lastOverflowed;
    } catch (err) {
      console.warn("ringBuffer.pushFrame failed", err);
    }
  }

  function drainAll() {
    if (!ring) return [];
    const drained = ring.drainAll();
    logStatus("drain", { draining: true, force: true });
    return drained;
  }

  function getRing() {
    return ring;
  }

  return { init, pushFrame, drainAll, getRing, logStatus };
}

export function createWsAudioRuntime(options = {}) {
  const {
    AppState: providedAppState = undefined,
    initPcmSender,
    updateState = () => {},
    logStage = () => {},
    getSocket,
    WSClient: providedWsClient = null,
    getWsClient,
    sendAudioChunk,
    sendJSON,
    isAudioStreaming = () => false,
    canCaptureNow = () => true,
    isSenderPaused = () => false,
    setSenderPauseReason = () => {},
    applySenderPausedState = () => {},
    getCaptureStream = null,
    getVadController = () => null,
    getFirstChunkSeen,
    setFirstChunkSeen,
    getMicRecordingStartAt,
    setMicRecordingStartAt,
    getMicChunks,
    setMicChunks,
    getMicBytes,
    setMicBytes,
    updateMicRms,
    audioKeepaliveMs: initialAudioKeepaliveMs = AUDIO_KEEPALIVE_MS,
    onFirstClientAudioFrame = null,
    onClientAudioChunkSend = null,
    getCurrentTurnReqId = () => null,
  } = options;

  const getAppState = () => resolveAppState(providedAppState);

  __firstPcmFrameLogged = false;

  // gumFailed means the current captureRuntime session is invalid.
  // It should NOT permanently poison the entire tab.
  let gumFailed = false;
  let lastGumError = null;
  let lastErrorName = null;
  let lastTrackState = null;
  let lastConstraints = null;
  let micReacquireTimer = null;
  let micReacquireInFlight = false;
  let micReacquireFailures = 0;
  let micHardFailed = false;
  let pendingReacquireReason = null;
  let heartbeatMissingCount = 0;
  let firstPcmToWsLogged = false;
  let lastPcmDisabledLogAt = 0;

  // Global diagnostics for debugging
  if (typeof window !== "undefined") {
    try {
      window.__gumFailed = gumFailed;
      window.__askchipAudioDiag = {
        get gumFailed() { return gumFailed; },
        get lastGumError() { return lastGumError; },
        get lastErrorName() { return lastErrorName; },
        get lastTrackState() { return lastTrackState; },
        get lastConstraints() { return lastConstraints; },
      };
      window.__askchipRetryMic = () => scheduleMicReacquire("user_retry", 0);
    } catch (_) {}
  }

  function getCurrentTrack() {
    const stream = captureStreamResolved || pcmSender?.mediaStream || null;
    return stream?.getAudioTracks?.()[0] || null;
  }

  function logMicState(reason = "heartbeat") {
    const track = getCurrentTrack();
    try {
      logStage("client.mic.state", {
        reason,
        hasStream: Boolean(captureStreamResolved || pcmSender?.mediaStream),
        trackReadyState: track?.readyState || "missing",
        muted: track?.muted,
        enabled: track?.enabled,
        gumFailed,
        lastErrorName: lastErrorName || null,
      });
    } catch (_) {}
  }

  function stopCaptureTracks(reason = "reacquire") {
    const stream = captureStreamResolved || pcmSender?.mediaStream || null;
    if (!stream) {
      return;
    }
    try {
      stream.getTracks?.().forEach((track) => {
        try {
          track.onended = null;
        } catch (_) {}
        try {
          track.stop();
        } catch (_) {}
      });
      logStage("client.mic.tracks_stopped", { reason });
    } catch (_) {}
    captureStreamResolved = null;
  }

  function scheduleMicReacquire(reason = "gum_failed", debounceMs = 800) {
    if (reason === "user_retry") {
      micHardFailed = false;
      micReacquireFailures = 0;
      try {
        updateState({ micUnavailable: false });
      } catch (_) {}
    } else if (micHardFailed) {
      return;
    }
    if (micReacquireInFlight) {
      pendingReacquireReason = reason;
      return;
    }
    pendingReacquireReason = reason;
    if (micReacquireTimer) {
      clearTimeout(micReacquireTimer);
    }
    micReacquireTimer = setTimeout(() => {
      micReacquireTimer = null;
      const pendingReason = pendingReacquireReason || reason;
      pendingReacquireReason = null;
      try {
        logStage("client.mic.reacquire.triggered", {
          reason: pendingReason,
          trackReadyState: getCurrentTrack()?.readyState || "missing",
          audioCtxState: audioCtx?.state || "unknown",
        });
      } catch (_) {}
      micReacquire(pendingReason);
    }, debounceMs);
  }

  function markGumFailed(reason, detail = {}) {
    gumFailed = true;
    lastGumError = detail?.error || detail?.message || null;
    lastErrorName = detail?.errorName || detail?.name || lastErrorName;
    lastTrackState = detail?.trackState || lastTrackState;
    lastConstraints = detail?.constraints || lastConstraints;
    try {
      logStage("client.mic.gum_failed", { reason, ...detail });
    } catch (_) {}
    if (typeof window !== "undefined") {
      try {
        window.__gumFailed = gumFailed;
      } catch (_) {}
    }
    scheduleMicReacquire(reason);
  }

  async function micReacquire(reason = "gum_failed") {
    if (micReacquireInFlight) {
      return null;
    }
    micReacquireInFlight = true;
    try {
      updateState({ micReacquireInFlight: true });
    } catch (_) {}
    micReacquireFailures += 1;
    let lastErrorMessage = null;
    let lastErrorNameLocal = null;
    let reacquireSucceeded = false;
    try {
      try {
        logStage("client.mic.reacquire.start", { reason });
      } catch (_) {}

      stopCaptureTracks("reacquire_start");
      lastTrackState = null;
      lastConstraints = {
        audio: {
          channelCount: { ideal: 1 },
          sampleRate: { ideal: 48000 },
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
        },
        video: false,
      };

      // Recreate MediaStream from shared mic hardware
      let newStream = null;
      const ensureHardware = typeof window !== "undefined" ? window.ensureMicHardware : null;
      if (typeof ensureHardware === "function") {
        newStream = await ensureHardware(lastConstraints);
      }
      if (!newStream) {
        lastErrorMessage = "hardware_unavailable";
        lastErrorNameLocal = "NotFoundError";
        markGumFailed("hardware_unavailable_reacquire", { constraints: lastConstraints });
        return null;
      }
      const track = newStream?.getAudioTracks?.()[0] || null;
      lastTrackState = track?.readyState || lastTrackState;

      if (track) {
        // For post-greet sessions, we want the track enabled.
        track.enabled = true;
      }
      if (track?.readyState === "ended") {
        lastErrorMessage = "track_ended";
        lastErrorNameLocal = "TrackEndedError";
        markGumFailed("track_ended_immediately_reacquire", {
          trackState: track?.readyState || null,
          constraints: lastConstraints,
        });
        return null;
      }
      if (track) {
        try {
          track.onended = () => {
            scheduleMicReacquire("track_ended", 800);
          };
        } catch (_) {}
      }

      const attempts = micReacquireFailures;
      captureStreamResolved = newStream;
      gumFailed = false;
      micHardFailed = false;
      micReacquireFailures = 0;
      lastGumError = null;
      lastErrorName = null;
      if (typeof window !== "undefined") window.__gumFailed = gumFailed;

      // Replace PCM sender stream if exists
      if (pcmSender && typeof pcmSender.replaceStream === "function") {
        try {
          pcmSender.replaceStream(newStream);
          logStage("client.mic.reacquire.sender_replaced", {});
        } catch (err) {
          logStage("client.mic.reacquire.sender_replace_failed", { err: String(err) });
        }
      }

      // Recreate AudioContext if suspended or closed
      try {
        if (audioCtx?.state === "suspended" || audioCtx?.state === "closed") {
          logStage("client.mic.reacquire.recreate_audioctx", { prev: audioCtx?.state });
          wsDiag("audio_warmup", { state: audioCtx?.state });
          audioCtx = getMicAudioContext();
        }
      } catch (err) {
        logStage("client.mic.reacquire.audioctx_failed", { err: String(err) });
      }

      try {
        logStage("client.mic.reacquire.result", {
          ok: true,
          errorName: null,
          errorMessage: null,
          attempts,
        });
      } catch (_) {}
      logMicState("reacquire_success");
      try {
        updateState({ micUnavailable: false });
      } catch (_) {}
      updatePcmSenderState("mic_reacquire_success");
      if (pendingReacquireReason) {
        const pending = pendingReacquireReason;
        pendingReacquireReason = null;
        scheduleMicReacquire(pending, 0);
      }

      reacquireSucceeded = true;
      return newStream;
    } catch (err) {
      lastErrorMessage = err?.message || String(err);
      lastErrorNameLocal = err?.name || "gum_failed";
      gumFailed = true;
      lastGumError = lastErrorMessage;
      logStage("client.mic.reacquire.failed", { err: String(err) });
      return null;
    } finally {
      if (micReacquireInFlight) {
        micReacquireInFlight = false;
        try {
          updateState({ micReacquireInFlight: false });
        } catch (_) {}
        if (!reacquireSucceeded) {
          try {
            logStage("client.mic.reacquire.result", {
              ok: false,
              errorName: lastErrorNameLocal || lastErrorName || null,
              errorMessage: lastErrorMessage || lastGumError || null,
              attempts: micReacquireFailures,
            });
          } catch (_) {}
          lastErrorName = lastErrorNameLocal || lastErrorName;
          if (micReacquireFailures >= 3) {
            micHardFailed = true;
            gumFailed = true;
            updatePcmSenderState("mic_reacquire_hard_failed");
            recordClientBannerEvent("mic.unavailable", {
              message: "Microphone unavailable — click to retry",
              attempts: micReacquireFailures,
            });
            try {
              updateState({ micUnavailable: true });
            } catch (_) {}
          }
          logMicState("reacquire_failed");
        }
      }
    }
  }

  try {
    console.log("AskChip ws_audio_runtime loaded", {
      hasSendAudioChunk: typeof sendAudioChunk === "function",
      hasIsAudioStreaming: typeof isAudioStreaming === "function",
    });
  } catch (_) {}

  // Diagnostic: when true, we bypass the normal PCM sender gate and always
  // enable the PCM sender while ASR is ready. This should only be used for
  // short, controlled tests.
  const FORCE_PCM_SEND = false; // set to true temporarily for diagnostics

  let localMicChunks = 0;
  let localMicBytes = 0;
  let localFirstChunkSeen = false;
  let localMicRecordingStartAt = null;
  let warnedMissingReqId = false;
  let pcmSenderStateLast = null;
  let dropSummaryReqId = null;
  const dropSummarySignatures = new Set();
  let firstRuntimeFrameLogged = false;
  let captureStreamProvider = typeof getCaptureStream === "function" ? getCaptureStream : null;
  let captureStreamResolved = null;

  function logSenderDecision(detail = {}) {
    if (!PCM_SENDER_DEBUG) {
      return;
    }
    try {
      logStage("client.pcm_sender.decision", detail);
    } catch (_) {
      // Do not let logging break the audio path
    }
  }

  function logWsAudioDropOnce(meta, reason) {
    const reqIdCandidate = typeof meta?.reqId === "string" && meta.reqId
      ? meta.reqId
      : null;
    const currentReqId = reqIdCandidate || (typeof getCurrentTurnReqId === "function"
      ? (getCurrentTurnReqId() || null)
      : null);
    if (currentReqId && currentReqId !== dropSummaryReqId) {
      dropSummaryReqId = currentReqId;
      dropSummarySignatures.clear();
    }

    wsAudioDropCount += 1;
    if (wsAudioDropCount <= WS_AUDIO_DROP_LOG_LIMIT) {
      try {
        logStage("client.ws.audio_drop", {
          lane: meta?.lane || "mic",
          reqId: currentReqId,
          reason: reason || "send_failed",
          count: wsAudioDropCount,
        });
      } catch (_) {}
    } else if (wsAudioDropCount === WS_AUDIO_DROP_LOG_LIMIT + 1) {
      try {
        logStage("client.ws.audio_drop_summary", {
          lane: meta?.lane || "mic",
          total: wsAudioDropCount,
        });
      } catch (_) {}
    }

    const dropSignature = `${currentReqId || "none"}|${reason || "unknown"}`;
    if (!dropSummarySignatures.has(dropSignature)) {
      dropSummarySignatures.add(dropSignature);
      try {
        const gateSnapshot = getPcmSenderGateSnapshot();
        logStage("client.audio_chunk_drop_summary", {
          reason: reason || "send_failed",
          lane: meta?.lane || "mic",
          reqId: currentReqId,
          phase: gateSnapshot?.voicePhase || gateSnapshot?.phase || null,
          wsPhase: gateSnapshot?.wsPhase || null,
          asrReady: gateSnapshot?.asrReady,
          senderPaused: gateSnapshot?.senderPaused,
          base_enabled: gateSnapshot?.base_enabled,
          hasStream: gateSnapshot?.hasStream,
          shouldSend: gateSnapshot?.shouldSend,
          isAudioStreaming: gateSnapshot?.isAudioStreaming,
        });
      } catch (_) {}
    }
  }

  const resolveWsClient = () => {
    if (typeof getWsClient === "function") {
      try {
        const candidate = getWsClient();
        if (candidate) return candidate;
      } catch (err) {
        console.warn("getWsClient failed", err);
      }
    }
    return providedWsClient;
  };

  const resolveSocket = () => {
    if (typeof getSocket === "function") {
      try {
        const sock = getSocket();
        if (sock) return sock;
      } catch (err) {
        console.warn("getSocket failed", err);
      }
    }
    const wsClient = resolveWsClient();
    return wsClient?._ws || null;
  };

  const getMicChunksValue = () => {
    if (typeof getMicChunks === "function") {
      try {
        const value = getMicChunks();
        if (Number.isFinite(value)) {
          return Number(value);
        }
      } catch (err) {
        console.warn("getMicChunks failed", err);
      }
    }
    return localMicChunks;
  };

  const setMicChunksValue = (value) => {
    if (typeof setMicChunks === "function") {
      try {
        setMicChunks(value);
      } catch (err) {
        console.warn("setMicChunks failed", err);
      }
    }
    localMicChunks = value;
  };

  const getMicBytesValue = () => {
    if (typeof getMicBytes === "function") {
      try {
        const value = getMicBytes();
        if (Number.isFinite(value)) {
          return Number(value);
        }
      } catch (err) {
        console.warn("getMicBytes failed", err);
      }
    }
    return localMicBytes;
  };

  const setMicBytesValue = (value) => {
    if (typeof setMicBytes === "function") {
      try {
        setMicBytes(value);
      } catch (err) {
        console.warn("setMicBytes failed", err);
      }
    }
    localMicBytes = value;
  };

  const readFirstChunkSeen = () => {
    if (typeof getFirstChunkSeen === "function") {
      try {
        return Boolean(getFirstChunkSeen());
      } catch (err) {
        console.warn("getFirstChunkSeen failed", err);
      }
    }
    return localFirstChunkSeen;
  };

  const writeFirstChunkSeen = (value) => {
    const desired = Boolean(value);
    if (typeof setFirstChunkSeen === "function") {
      try {
        setFirstChunkSeen(desired);
      } catch (err) {
        console.warn("setFirstChunkSeen failed", err);
      }
    }
    localFirstChunkSeen = desired;
    if (!desired) {
      firstPcmToWsLogged = false;
    }
  };

  const resetFirstChunkTelemetry = () => {
    writeFirstChunkSeen(false);
    firstRuntimeFrameLogged = false;
    __firstPcmFrameLogged = false;
  };

  const readMicRecordingStartAt = () => {
    if (typeof getMicRecordingStartAt === "function") {
      try {
        const value = getMicRecordingStartAt();
        if (Number.isFinite(value)) {
          return Number(value);
        }
      } catch (err) {
        console.warn("getMicRecordingStartAt failed", err);
      }
    }
    return localMicRecordingStartAt;
  };

  const safeSendAudioChunk = (payload, meta = {}) => {
    lastPcmSendDropReason = null;
    const currentReqId = typeof getCurrentTurnReqId === "function"
      ? (getCurrentTurnReqId() || null)
      : null;
    const enrichedMeta = meta && typeof meta === "object" ? { ...meta } : {};
    const wsPhase = getAppState()?.wsPhase || null;
    try {
      logStage("client.audio_chunk_attempt", {
        length: payload?.byteLength || payload?.length || null,
        ws_ready: resolveSocket()?.readyState,
        turnId: enrichedMeta.turnId || null,
      });
    } catch (_) {}
    if (!currentReqId && !warnedMissingReqId) {
      warnedMissingReqId = true;
      try {
        console.warn("ws_audio_runtime: audio chunk without active req_id; sending anyway");
      } catch (_) {}
      try {
        logStage("client.audio_chunk_missing_req_id", {
          lane: enrichedMeta.lane || "mic",
          reqId: null,
          reason: "missing_reqId",
          turnId: enrichedMeta.turnId || null,
        });
      } catch (_) {}
    } else if (currentReqId) {
      warnedMissingReqId = false;
      if (!enrichedMeta.reqId) {
        enrichedMeta.reqId = currentReqId;
      }
    }
    if (!enrichedMeta.sampleRateHz && typeof enrichedMeta.sampleRate === "number") {
      enrichedMeta.sampleRateHz = enrichedMeta.sampleRate;
    }
    const socket = resolveSocket?.();
    const socketOpen = !!socket && socket.readyState === WebSocket.OPEN;

    if (!socketOpen || wsPhase === "closing" || wsPhase === "closed") {
      lastPcmSendDropReason = "ws_not_open";
      logWsAudioDropOnce(enrichedMeta, "ws_not_open");
      return false;
    }
    if (typeof sendAudioChunk === "function") {
      try {
        logPcmEnergy(payload, enrichedMeta);
        sendAudioChunk(payload, enrichedMeta);
        try {
          logStage("client.audio_chunk_send", {
            lane: enrichedMeta.lane || "mic",
            reqId: enrichedMeta.reqId || null,
            keepalive: !!enrichedMeta.keepalive,
            sampleRate: enrichedMeta.sampleRateHz || enrichedMeta.sampleRate || null,
            source: "delegate",
            turnId: enrichedMeta.turnId || null,
            sent: true,
          });
        } catch (_) {}
        return true;
      } catch (err) {
        console.warn("sendAudioChunk delegate failed", err);
        lastPcmSendDropReason = "delegate_exception";
        logWsAudioDropOnce(enrichedMeta, "delegate_exception");
      }
    }
    const wsClient = resolveWsClient();
    if (wsClient && typeof wsClient.sendAudioChunk === "function") {
      try {
        logPcmEnergy(payload, enrichedMeta);
        wsClient.sendAudioChunk(payload, enrichedMeta);
        try {
          logStage("client.audio_chunk_send", {
            lane: enrichedMeta.lane || "mic",
            reqId: enrichedMeta.reqId || null,
            keepalive: !!enrichedMeta.keepalive,
            sampleRate: enrichedMeta.sampleRateHz || enrichedMeta.sampleRate || null,
            source: "wsclient",
            turnId: enrichedMeta.turnId || null,
          });
        } catch (_) {}
        return true;
      } catch (err) {
        console.warn("WSClient.sendAudioChunk failed", err);
        lastPcmSendDropReason = "send_exception";
        logWsAudioDropOnce(enrichedMeta, "send_exception");
      }
    }
    try {
      logStage("client.audio_chunk_send_failed", {
        lane: enrichedMeta.lane || "mic",
        reqId: enrichedMeta.reqId || null,
        keepalive: !!enrichedMeta.keepalive,
        sampleRate: enrichedMeta.sampleRateHz || enrichedMeta.sampleRate || null,
        turnId: enrichedMeta.turnId || null,
      });
    } catch (_) {}
    lastPcmSendDropReason = lastPcmSendDropReason || "no_delegate";
    logWsAudioDropOnce(enrichedMeta, "no_delegate");
    return false;
  };

  const safeSendJSON = (payload) => {
    if (typeof sendJSON === "function") {
      try {
        sendJSON(payload);
        return true;
      } catch (err) {
        console.warn("sendJSON delegate failed", err);
      }
    }
    const wsClient = resolveWsClient();
    if (wsClient && typeof wsClient.sendJSON === "function") {
      try {
        wsClient.sendJSON(payload);
        return true;
      } catch (err) {
        console.warn("WSClient.sendJSON failed", err);
      }
    }
    return false;
  };

  const updateMicMeter = (rms) => {
    if (typeof updateMicRms === "function") {
      try {
        updateMicRms(rms);
      } catch (err) {
        console.warn("updateMicRms failed", err);
      }
    } else {
      const AppState = getAppState();
      if (AppState && typeof AppState === "object") {
        AppState.micRms = rms;
      }
    }
    if (typeof window !== "undefined") {
      try {
        window.StatusBar?.updateMeter?.(rms);
      } catch (err) {
        console.warn("StatusBar.updateMeter failed", err);
      }
    }
  };

  try {
    if (typeof getMicChunks === "function") {
      const initialChunks = Number(getMicChunks());
      if (Number.isFinite(initialChunks)) {
        localMicChunks = initialChunks;
      }
    }
  } catch (err) {
    console.warn("initial getMicChunks failed", err);
  }
  try {
    if (typeof getMicBytes === "function") {
      const initialBytes = Number(getMicBytes());
      if (Number.isFinite(initialBytes)) {
        localMicBytes = initialBytes;
      }
    }
  } catch (err) {
    console.warn("initial getMicBytes failed", err);
  }
  try {
    if (typeof getFirstChunkSeen === "function") {
      localFirstChunkSeen = Boolean(getFirstChunkSeen());
    }
  } catch (err) {
    console.warn("initial getFirstChunkSeen failed", err);
  }
  try {
    if (typeof getMicRecordingStartAt === "function") {
      const initialStart = Number(getMicRecordingStartAt());
      if (Number.isFinite(initialStart)) {
        localMicRecordingStartAt = initialStart;
      }
    }
  } catch (err) {
    console.warn("initial getMicRecordingStartAt failed", err);
  }

  const initialAppState = getAppState();
  const telemetryEnabled = Boolean(initialAppState?.policy?.deepgramV3TelemetryEnabled ?? true);
  const deepgramV3Enabled = Boolean(
    initialAppState?.policy?.deepgramV3Enabled ?? initialAppState?.deepgramV3Enabled ?? false,
  );
  const deepgramV3TurnControlEnabled = Boolean(
    initialAppState?.policy?.deepgramV3TurnControlEnabled ?? false,
  ) && deepgramV3Enabled;
  const asrRate = Number.isFinite(initialAppState?.targetSampleRate)
    ? Number(initialAppState.targetSampleRate)
    : PCM_TARGET_SAMPLE_RATE;
  const ringBufferManager = createRingBufferManager(asrRate);
  const preSpeechBufferMs = Number.isFinite(initialAppState?.preSpeechBufferMs)
    ? initialAppState.preSpeechBufferMs
    : DEFAULT_PRE_SPEECH_BUFFER_MS;
  const pcmRing = ringBufferManager.init(preSpeechBufferMs, asrRate);
  const ringStatusMinMs = 15000;
  const ringStatusMaxMs = 30000;
  let ringStatusTimerId = null;
  const scheduleRingStatusLog = () => {
    if (!telemetryEnabled) {
      return;
    }
    const jitter = Math.floor(Math.random() * (ringStatusMaxMs - ringStatusMinMs + 1));
    const delay = ringStatusMinMs + jitter;
    ringStatusTimerId = setTimeout(() => {
      ringStatusTimerId = null;
      ringBufferManager.logStatus("interval");
      scheduleRingStatusLog();
    }, delay);
  };
  scheduleRingStatusLog();

  let pcmSender = null;
  let pcmSenderInitPromise = null;
  let pcmLastSeq = 0;
  let pcmLastBytes = null;
  let pcmSampleRate = asrRate;
  let pcmHardwareSampleRate = null;
  let audioCtx = null;
  let baseEnabled = false;
  let baseEnabledReason = "boot";
  let pcmSenderAutoUnpauseGuard = false;
  let micKeepaliveTimerId = null;
  let micLastChunkAt = 0;
  let lastRealAudioAt = 0;
  let lastPcmSendDropReason = null;
  localFirstChunkSeen = readFirstChunkSeen();
  localMicRecordingStartAt = readMicRecordingStartAt();
  audioKeepaliveMs = Number.isFinite(initialAudioKeepaliveMs) && initialAudioKeepaliveMs > 0
    ? initialAudioKeepaliveMs
    : AUDIO_KEEPALIVE_MS;

  if (telemetryEnabled) {
    setInterval(() => {
      const track = getCaptureStream?.()?.getAudioTracks?.()[0] || getCurrentTrack();
      try {
        const trackReadyState = track?.readyState || "missing";
        logStage("client.mic.heartbeat", {
          trackReadyState,
          enabled: track?.enabled,
          muted: track?.muted,
          audioCtxState: audioCtx?.state || "unknown",
          isInterrupted: audioCtx?.state === "interrupted",
          gumFailed,
        });
        logMicState("heartbeat");
        if (trackReadyState === "missing") {
          heartbeatMissingCount += 1;
          if (heartbeatMissingCount > 1) {
            scheduleMicReacquire("heartbeat_track_missing", 800);
          }
        } else {
          heartbeatMissingCount = 0;
        }
        if (audioCtx?.state === "interrupted") {
          try {
            audioCtx.resume();
            logStage("client.audio_context.recovered_from_interrupted", {});
          } catch (_) {}
        }
      } catch (_) {}
    }, 5000);
  }

  if (typeof navigator !== "undefined" && navigator.mediaDevices?.addEventListener) {
    try {
      navigator.mediaDevices.addEventListener("devicechange", () => {
        scheduleMicReacquire("devicechange", 800);
      });
    } catch (_) {}
  }

  function clearAudioKeepaliveTimer() {
    if (micKeepaliveTimerId) {
      clearTimeout(micKeepaliveTimerId);
      micKeepaliveTimerId = null;
    }
  }

  function resetSilenceSuppression() {
    // Phase 1 (Google Flow V3): silence pause reasons are removed. This stub remains
    // for API compatibility with previous implementations and test helpers.
  }

  function sendAudioKeepaliveChunk(now) {
    const sampleRate = 16000;
    const samples = Math.max(
      1,
      Math.round((sampleRate * AUDIO_KEEPALIVE_CHUNK_MS) / 1000),
    );
    if (!samples) {
      return false;
    }
    const silenceChunk = new Int16Array(samples);
    try {
      logStage("client.audio_keepalive_attempt", {
        samples,
        sampleRate,
      });
    } catch (_) {}
    const sent = safeSendAudioChunk(silenceChunk, {
      lane: "mic",
      keepalive: true,
      sampleRateHz: sampleRate,
    });
    if (sent) {
      micLastChunkAt = now;
      try {
        logStage("client.audio_keepalive", { bytes: silenceChunk.byteLength, interval_ms: audioKeepaliveMs });
      } catch (_) {}
    } else {
      try {
        logStage("client.audio_keepalive_failed", {
          samples,
          sampleRate,
        });
      } catch (_) {}
    }
    return sent;
  }

  function maybeSendAudioKeepalive(now) {
    const result = { sentKeepalive: false, idleTimedOut: false };
    const ws = resolveSocket();
    if (!ws) {
      return result;
    }
    if (typeof WebSocket !== "undefined" && ws.readyState !== WebSocket.OPEN) {
      return result;
    }
    const AppState = getAppState();
    const listening = Boolean(AppState?.listening);
    const streaming = typeof isAudioStreaming === "function" ? isAudioStreaming() : true;
    if (!(listening && streaming)) {
      return result;
    }
    let idleDuration = null;
    if (Number.isFinite(audioKeepaliveIdleMs) && audioKeepaliveIdleMs > 0 && lastRealAudioAt > 0) {
      idleDuration = now - lastRealAudioAt;
      if (idleDuration >= audioKeepaliveIdleMs) {
        setSenderPauseReason("idle_timeout", true);
        updatePcmSenderState();
        clearAudioKeepaliveTimer();
        result.idleTimedOut = true;
        return result;
      }
    }
    const shouldSendKeepalive = now - micLastChunkAt >= audioKeepaliveMs;
    if (!shouldSendKeepalive) {
      return result;
    }
    try {
      logStage("client.audio_keepalive_eligible", {
        listening,
        streaming,
        idle_ms: idleDuration,
      });
    } catch (_) {}
    if (sendAudioKeepaliveChunk(now)) {
      result.sentKeepalive = true;
      return result;
    }
    try {
      if (safeSendJSON({ type: "client.ping" })) {
        logStage("client.ping", { lane: "mic", fallback: true });
      }
    } catch (err) {
      console.warn("client.ping send failed", err);
    }
    return result;
  }

  function scheduleAudioKeepalive() {
    clearAudioKeepaliveTimer();
    if (!Number.isFinite(audioKeepaliveMs) || audioKeepaliveMs <= 0) {
      return;
    }
    micKeepaliveTimerId = setTimeout(() => {
      micKeepaliveTimerId = null;
      const now = Date.now();
      const result = maybeSendAudioKeepalive(now);
      if (!result.idleTimedOut) {
        scheduleAudioKeepalive();
      }
    }, audioKeepaliveMs);
  }

  function sendAudioKeepaliveNow() {
    const now = Date.now();
    const result = maybeSendAudioKeepalive(now);
    if (!result.idleTimedOut) {
      scheduleAudioKeepalive();
    }
    return result.sentKeepalive;
  }

  function recordRecorderChunk(timestampMs) {
    const now = Number.isFinite(timestampMs) ? timestampMs : Date.now();
    const AppState = getAppState();
    const currentCount = typeof AppState?.chunkCount === "number"
      ? AppState.chunkCount
      : (typeof AppState?.getState === "function" ? (AppState.getState().chunkCount || 0) : 0);
    const nextCount = currentCount + 1;
    lastRealAudioAt = now;
    if (AppState && typeof AppState === "object") {
      AppState.chunkCount = nextCount;
      AppState.lastChunkTs = now;
    }
    updateState({ chunkCount: nextCount, lastChunkTs: now });
  }

  function chunk20ms(int16, sampleRate) {
    const size = Math.round(sampleRate * 0.02);
    if (!Number.isFinite(size) || size <= 0) {
      return [];
    }
    const chunks = [];
    for (let i = 0; i + size <= int16.length; i += size) {
      chunks.push(int16.subarray(i, i + size));
    }
    return chunks;
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

  function computeHardGateSnapshot({ wsPhase, fatalError, wsReadyState }) {
    const socketOpen = typeof wsReadyState === "number"
      ? wsReadyState === WebSocket.OPEN
      : true;

    const phaseReady = typeof wsPhase === "string" ? WS_READY_PHASES.has(wsPhase) : true;

    const wsReady = socketOpen && phaseReady;
    let allowed = wsReady && !fatalError;
    let reason = "ok";
    if (!wsReady) {
      allowed = false;
      reason = "ws_not_ready";
    } else if (fatalError) {
      allowed = false;
      reason = "fatal_error";
    }
    return { allowed, reason, wsPhase, wsReadyState };
  }

  let lastHardGateLogged = null;
  let lastPcmGateLoggedSignature = null;
  function emitHardGateSnapshot({
    hardGate,
    gateSnapshot,
    reason = "gate_change",
    force = false,
  } = {}) {
    if (!hardGate || !gateSnapshot) return;
    const signature = [
      hardGate.allowed ? "A1" : "A0",
      hardGate.reason || "unknown",
      gateSnapshot.shouldSend ? "S1" : "S0",
      gateSnapshot.baseGate ? "B1" : "B0",
      gateSnapshot.hasStream ? "H1" : "H0",
      gateSnapshot.senderPaused ? "P1" : "P0",
      gateSnapshot.wsPhase || "null",
    ].join("|");
    if (!force && lastHardGateLogged === signature) {
      return;
    }
    lastHardGateLogged = signature;
    try {
      logStage("client.deepgram_v3.hard_gate_snapshot", {
        reason,
        allowed: hardGate.allowed ?? false,
        hard_gate_reason: hardGate.reason || "unknown",
        voicePhase: gateSnapshot.phaseValue || null,
        wsPhase: gateSnapshot.wsPhase || null,
        wsReadyState: typeof hardGate.wsReadyState === "number" ? hardGate.wsReadyState : null,
        asrReady: gateSnapshot.asrReady,
        senderPaused: gateSnapshot.senderPaused,
        base_enabled: gateSnapshot.baseGate,
        hasStream: gateSnapshot.hasStream,
        shouldSend: gateSnapshot.shouldSend,
        isAudioStreaming: gateSnapshot.isAudioStreaming,
      });
    } catch (_) {}
  }

  let speechSeenThisTurn = false;
  let lastSpeechSeenReqId = null;
  let currentTurnId = null;
  let nextTurnId = 1;
  let speechStartSeen = false;
  let speechNeverMarkedSeenLogged = false;

  function allocateTurnId() {
    return String(nextTurnId++);
  }
  function shouldSendFrameSoftGate({ vadState, speechSeen }) {
    // Soft gate is telemetry-only for the AskChip mic lane; ASR/TurnEngine decide
    // end-of-speech and turn boundaries instead of client-side VAD drops.
    if (!vadState) {
      return { shouldSend: true, vadLikelySpeech: false, rmsAtTrigger: null, reason: "no_vad_state" };
    }
    const state = typeof vadState?.state === "string" ? vadState.state : null;
    const vadLikelySpeech = Boolean(
      vadState?.isSpeech ||
      vadState?.speech ||
      vadState?.speaking ||
      state === "speech" ||
      state === "voice" ||
      (state && state !== "silence" && state !== "quiet")
    );
    if (!speechSeen && vadLikelySpeech) {
      speechStartSeen = true;
    }
    const shouldSend = vadLikelySpeech;
    const reason = vadLikelySpeech ? "vad_speech" : "vad_silence";
    const rmsAtTrigger = !speechSeen && vadLikelySpeech ? vadState?.rms ?? vadState?.rmsDb ?? null : null;
    return { shouldSend, vadLikelySpeech, rmsAtTrigger, reason };
  }

  const softGateTelemetryIntervalFrames = 10;
  let lastSoftGateTelemetryReason = null;
  let softGateTelemetryFrameCounter = 0;

  function maybeEmitSoftGateTelemetry({ reason, vadLikelySpeech, wsPhase, appPhase, wsReadyState }) {
    const safeReason = reason || "unknown";
    const reasonChanged = safeReason !== lastSoftGateTelemetryReason;
    softGateTelemetryFrameCounter += 1;
    const intervalHit = softGateTelemetryFrameCounter >= softGateTelemetryIntervalFrames;
    if (!reasonChanged && !intervalHit) {
      return;
    }
    lastSoftGateTelemetryReason = safeReason;
    softGateTelemetryFrameCounter = 0;
    emitPolicyHook("soft_gate_telemetry", {
      reason: safeReason,
      allowed: true,
      vadLikelySpeech: Boolean(vadLikelySpeech),
      wsPhase,
      appPhase,
      wsReadyState,
    });
  }

  function maybeLogSpeechNeverMarkedSeen() {
    if (speechNeverMarkedSeenLogged || !speechStartSeen || speechSeenThisTurn) {
      return;
    }
    speechNeverMarkedSeenLogged = true;
    try {
      logStage("client.deepgram_v3.speech_never_marked_seen", { turnId: currentTurnId || null });
    } catch (_) {}
  }

  function markSpeechSeen({
    rmsAtTrigger = null,
    framesSinceGreet = null,
    reqId = null,
    turnId = null,
  }) {
    speechSeenThisTurn = true;
    lastSpeechSeenReqId = reqId || lastSpeechSeenReqId || null;
    try {
      logStage("client.deepgram_v3.speech_seen_this_turn", {
        speechSeenThisTurn: true,
        rmsAtTrigger,
        framesSinceGreet,
        reqId: lastSpeechSeenReqId,
        turnId: turnId || currentTurnId || null,
        ts_ms: Date.now(),
        ts_mono_ms: typeof performance?.now === "function" ? performance.now() : null,
      });
    } catch (_) {}
  }

  function resetTurnForNextUser() {
    speechSeenThisTurn = false;
    speechStartSeen = false;
    speechNeverMarkedSeenLogged = false;
    currentTurnId = null;
    lastSpeechSeenReqId = null;
    lastSoftGateTelemetryReason = null;
    softGateTelemetryFrameCounter = 0;
    try { setMicChunksValue(0); } catch (_) {}
    try { setMicBytesValue(0); } catch (_) {}
    try { resetFirstChunkTelemetry(); } catch (_) {}
    try { updateState({ chunkCount: 0, lastChunkTs: null }); } catch (_) {}
  }

  const pcmSummaryWindowMs = 5000;
  let pcmSummaryCounters = {
    framesAttempted: 0,
    framesSent: 0,
    framesDropped: 0,
    dropReasons: {},
  };
  let pcmSummaryGateReady = false;
  let lastPcmSummaryAt = 0;

  const policyHookLogLimitPerReason = 3;
  let lastPolicyHookTurnId = null;
  let policyHookCounts = new Map();

  function shouldLogPolicyHook(reason) {
    const turnId = typeof getCurrentTurnReqId === "function" ? getCurrentTurnReqId() || null : null;
    if (turnId !== lastPolicyHookTurnId) {
      policyHookCounts.clear();
      lastPolicyHookTurnId = turnId;
    }
    const key = `${turnId || "__none__"}:${reason || "unknown"}`;
    const count = policyHookCounts.get(key) || 0;
    if (count >= policyHookLogLimitPerReason) {
      return false;
    }
    policyHookCounts.set(key, count + 1);
    return true;
  }

  function maybeLogPcmSummary(force = false) {
    const now = Date.now();
    if (!force && now - lastPcmSummaryAt < pcmSummaryWindowMs) {
      return;
    }
    if (pcmSummaryCounters.framesAttempted <= 0) {
      return;
    }
    const dropReasons = pcmSummaryCounters.dropReasons || {};
    const dropEntries = Object.entries(dropReasons)
      .filter(([, count]) => Number.isFinite(count) && count > 0)
      .sort((a, b) => b[1] - a[1]);
    const topEntries = dropEntries.slice(0, 3);
    const otherTotal = dropEntries.slice(3).reduce((sum, [, count]) => sum + count, 0);
    const droppedByReason = {};
    for (const [reason, count] of topEntries) {
      droppedByReason[reason] = count;
    }
    if (otherTotal > 0) {
      droppedByReason.other = otherTotal;
    }
    const summaryAppState = getAppState();
    const summarySocket = resolveSocket();
    const summaryWsReadyState = summarySocket?.readyState ?? null;
    lastPcmSummaryAt = now;
    try {
      logStage("client.deepgram_v3.pcm_send_summary", {
        windowMs: pcmSummaryWindowMs,
        framesAttempted: pcmSummaryCounters.framesAttempted,
        framesSent: pcmSummaryCounters.framesSent,
        framesDropped: pcmSummaryCounters.framesDropped,
        droppedByReason,
        gateReady: pcmSummaryGateReady,
        wsPhase: typeof summaryAppState?.wsPhase === "string" ? summaryAppState.wsPhase : null,
        appPhase: typeof summaryAppState?.phase === "string" ? summaryAppState.phase : null,
        wsReadyState: typeof summaryWsReadyState === "number" ? summaryWsReadyState : null,
      });
    } catch (_) {}
    pcmSummaryCounters = {
      framesAttempted: 0,
      framesSent: 0,
      framesDropped: 0,
      dropReasons: {},
    };
    pcmSummaryGateReady = false;
  }

  function recordPcmFrameOutcome({ attempted = 0, sent = 0, dropped = 0, dropReason = null }) {
    pcmSummaryCounters.framesAttempted += attempted;
    pcmSummaryCounters.framesSent += sent;
    pcmSummaryCounters.framesDropped += dropped;
    if (dropReason) {
      const key = String(dropReason);
      pcmSummaryCounters.dropReasons[key] = (pcmSummaryCounters.dropReasons[key] || 0) + dropped;
    }
    maybeLogPcmSummary(false);
  }

  function emitPolicyHook(reason, detail = {}) {
    if (!shouldLogPolicyHook(reason)) {
      return;
    }
    try {
      logStage("client.deepgram_v3.policy_hook", { reason, ...detail });
    } catch (_) {}
  }

  function sendPrerollChunks(prerollChunks, sampleRate, baseMeta = {}) {
    if (!Array.isArray(prerollChunks) || !prerollChunks.length) {
      return;
    }

    const sr = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : asrRate;
    const lane = typeof baseMeta.lane === "string" ? baseMeta.lane : "mic";
    const turnId = baseMeta.turnId || null;

    try {
      logStage("client.deepgram_v3.preroll_flush", {
        lane,
        turnId,
        chunks: prerollChunks.length,
        preRollMs: preSpeechBufferMs,
      });
    } catch (_) {}

    for (const payload of prerollChunks) {
      if (!(payload instanceof Int16Array) || !payload.length) {
        continue;
      }
      const sent = safeSendAudioChunk(payload, {
        ...baseMeta,
        lane,
        sampleRateHz: sr,
        chunkCount: 1,
        preRoll: true,
        preRollMs: preSpeechBufferMs,
        turnId,
      });
      if (sent) {
        recordPcmFrameOutcome({ attempted: 1, sent: 1 });
      } else {
        recordPcmFrameOutcome({
          attempted: 1,
          dropped: 1,
          dropReason: lastPcmSendDropReason || "preroll_send_failed",
        });
        try {
          logStage("client.audio_preroll_send_failed", { lane, turnId });
        } catch (_) {}
      }
    }
  }

  function primeAsrStreamFromRing(sid) {
    if (!pcmRing || typeof pcmRing.tailMillis !== "function") {
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
        for (const chunk of chunk20ms(tail, asrRate)) {
          if (chunk && chunk.length) {
            safeSendAudioChunk(chunk);
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
      console.warn("primeAsrStreamFromRing failed", err);
    }
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
    const AppState = getAppState();
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

    // Force a sender-state evaluation whenever we learn the sample rate.
    // This guarantees updatePcmSenderState runs at least once during setup.
    try {
      updatePcmSenderState("handleSampleRate");
    } catch (err) {
      try {
        console.warn("[ws_audio_runtime] updatePcmSenderState from handleSampleRate failed", err);
      } catch (_) {}
    }
  }

  function handlePcmFrame(frame, meta = {}) {
    if (!__firstPcmFrameLogged && frame instanceof Int16Array && frame.length) {
      __firstPcmFrameLogged = true;
      try {
        logStage("client.pcm.first_frame", {
          samples: frame.length,
          sampleRate: meta?.sampleRate || null,
        });
      } catch (_) {}
    }
    if (!frame) {
      return;
    }

    let wire = null;

    if (frame instanceof Int16Array && frame.length) {
      wire = frame;
    } else {
      if (isTypedArray(frame) && frame.BYTES_PER_ELEMENT && frame.BYTES_PER_ELEMENT !== 2) {
        console.warn("ws_audio_runtime: invalid PCM chunk, expected ArrayBuffer or TypedArray");
        try {
          logStage("client.pcm.invalid_chunk", {
            reason: "wrong_element_size",
            bytes_per_element: frame && frame.BYTES_PER_ELEMENT ? frame.BYTES_PER_ELEMENT : null,
            constructor: frame && frame.constructor ? frame.constructor.name : null,
          });
        } catch (_) {}
        return;
      }

      const buffer = toArrayBuffer(frame);
      if (!buffer) {
        console.warn("ws_audio_runtime: invalid PCM chunk, expected ArrayBuffer or TypedArray");
        try {
          logStage("client.pcm.invalid_chunk", {
            reason: "toArrayBuffer_failed",
            constructor: frame && frame.constructor ? frame.constructor.name : null,
            typeof: typeof frame,
          });
        } catch (_) {}
        return;
      }

      wire = new Int16Array(buffer);
    }

    if (!(wire instanceof Int16Array) || !wire.length) {
      return;
    }

    if (!pcmWarm && wire.length) {
      markPcmWarm();
    }

    const stream = captureStreamResolved || pcmSender?.mediaStream || null;
    const track = stream?.getAudioTracks?.()[0] || null;
    if (track && track.readyState === "ended" && !gumFailed) {
      markGumFailed("track_ended_windows11_device_switch", { trackState: track.readyState });
      return;
    }
    const metaSampleRate = Number(meta.sampleRate);
    if (Number.isFinite(metaSampleRate) && metaSampleRate > 0) {
      pcmSampleRate = metaSampleRate;
    }
    pcmLastSeq = Number.isFinite(meta.seq) ? Number(meta.seq) : pcmLastSeq;

    const currentSampleRate = Number.isFinite(pcmSampleRate) && pcmSampleRate > 0 ? pcmSampleRate : asrRate;
    const now = Date.now();

    if (!firstRuntimeFrameLogged && wire.length) {
      firstRuntimeFrameLogged = true;
      try {
        logStage("client.pcm.first_frame_runtime", {
          samples: wire.length,
          sampleRate: currentSampleRate || null,
        });
      } catch (_) {}
    }

    try {
      ringBufferManager.pushFrame(wire);
    } catch (e) {
      console.warn("pcmRing.push failed", e);
    }

    if (!isAudioStreaming()) {
      return;
    }

    if (!readFirstChunkSeen()) {
      writeFirstChunkSeen(true);
      let firstFrameMs = null;
      const startedAt = readMicRecordingStartAt();
      if (typeof startedAt === "number") {
        firstFrameMs = Math.max(0, Math.round(now - startedAt));
      }
      const firstFrameDetail = {
        seq: pcmLastSeq,
        bytes: wire.byteLength,
      };
      if (firstFrameMs !== null) {
        firstFrameDetail.ms_since_recording_start = firstFrameMs;
      }
      try { logStage("client.pcm.first_frame", firstFrameDetail); } catch {}
      try { logStage("client.audio_first_chunk", { bytes: wire.byteLength }); } catch {}
      if (typeof onFirstClientAudioFrame === "function") {
        try {
          onFirstClientAudioFrame({ ...firstFrameDetail, ts_ms: now });
        } catch (err) {
          console.warn("onFirstClientAudioFrame failed", err);
        }
      }
    }

    micLastChunkAt = now;
    scheduleAudioKeepalive();
    recordRecorderChunk(now);

    const frameTimestamp = Number.isFinite(meta.timestamp)
      ? meta.timestamp
      : ((typeof performance !== "undefined" && typeof performance.now === "function")
        ? performance.now()
        : now);

    const vadController = getVadController();
    if (vadController && typeof vadController.onPcmFrame === "function") {
      try {
        vadController.onPcmFrame(wire.buffer, frameTimestamp);
      } catch (err) {
        console.warn("VAD frame processing failed", err);
      }
    }

    let sumSq = 0;
    for (let i = 0; i < wire.length; i += 1) {
      const sample = wire[i] / 32768;
      sumSq += sample * sample;
    }
    if (wire.length) {
      const rms = Math.sqrt(sumSq / wire.length);
      updateMicMeter(rms);
    }
  }

  function handlePcmSend(chunk, meta = {}) {
    if (!(chunk instanceof Int16Array) || !chunk.length) {
      return;
    }
    const isKeepalive = Boolean(meta.keepalive);
    let prerollChunksToSend = null;
    if (gumFailed || micHardFailed) {
      try {
        const now = Date.now();
        if (now - lastPcmDisabledLogAt >= 2000) {
          lastPcmDisabledLogAt = now;
          logStage("client.pcm_sender.disabled_for_gum_failed", {
            gumFailed,
            micHardFailed,
            lastGumError,
            lastErrorName,
            lastTrackState,
          });
        }
      } catch (_) {}
      updatePcmSenderState("gum_failed_block_send");
      return;
    }
    wsDiag("pcm_send_attempt", {
      bytes: chunk?.byteLength,
      gumFailed,
      audioCtxState: audioCtx?.state,
    });
    try {
      logStage("client.audio_chunk", {
        bytes: chunk?.byteLength || 0,
        gumFailed,
        turnId: currentTurnId,
      });
    } catch (_) {}
    const chunkCount = Number.isFinite(meta.chunkCount) ? Number(meta.chunkCount) : 1;
    const currentReqId = typeof getCurrentTurnReqId === "function" ? getCurrentTurnReqId() : null;
    if (currentReqId && currentReqId !== lastSpeechSeenReqId) {
      maybeLogSpeechNeverMarkedSeen();
      speechStartSeen = false;
      speechSeenThisTurn = false;
      lastSpeechSeenReqId = currentReqId;
      currentTurnId = null;
    }

    const AppState = getAppState();
    const ws = resolveSocket();
    const wsReadyState = ws?.readyState ?? null;
    const wsPhase = typeof AppState?.wsPhase === "string" ? AppState.wsPhase : null;
    const phaseValue = typeof AppState?.phase === "string" ? AppState.phase : null;
    const hardGate = computeHardGateSnapshot({
      wsPhase,
      fatalError: gumFailed,
      wsReadyState,
    });
    const gateSnapshot = computePcmGateSnapshot();
    pcmSummaryGateReady = pcmSummaryGateReady || Boolean(hardGate?.allowed);
    emitHardGateSnapshot({
      hardGate,
      gateSnapshot,
      reason: hardGate.allowed ? "gate_check" : "hard_gate_drop",
      force: !hardGate.allowed,
    });
    if (!hardGate.allowed) {
      recordPcmFrameOutcome({
        attempted: chunkCount,
        dropped: chunkCount,
        dropReason: `hard_gate:${hardGate.reason || "unknown"}`,
      });
      emitPolicyHook("hard_gate_drop", { reason: hardGate.reason, wsPhase, appPhase: phaseValue, wsReadyState });
      return;
    }

    const vadState = typeof getVadController === "function" ? getVadController()?.getState?.() || null : null;
    const softDecision = isKeepalive
      ? { shouldSend: true, vadLikelySpeech: false, rmsAtTrigger: null, reason: "keepalive" }
      : shouldSendFrameSoftGate({ vadState, speechSeen: speechSeenThisTurn });

    if (!isKeepalive && !speechSeenThisTurn && softDecision.vadLikelySpeech) {
      const turnIdCandidate = typeof getCurrentTurnReqId === "function" ? getCurrentTurnReqId() : null;
      currentTurnId = turnIdCandidate && `${turnIdCandidate}`.length ? `${turnIdCandidate}` : allocateTurnId();
      markSpeechSeen({
        rmsAtTrigger: softDecision.rmsAtTrigger,
        framesSinceGreet: null,
        reqId: currentReqId,
        turnId: currentTurnId,
      });
      if (deepgramV3TurnControlEnabled) {
        // 1. START: Wake up the server first
        try {
          safeSendJSON({
            type: "client.turn_start",
            lane: "mic",
            turn_id: currentTurnId,
            pre_roll_ms: 0,
          });
        } catch (_) {}
        // 2. PREPARE AUDIO
        try {
          prerollChunksToSend = ringBufferManager.drainAll();
        } catch (_) {
          prerollChunksToSend = [];
        }
        // 3. AUDIO "DOUBLE TAP": Send now, and send again shortly to beat the race condition
        if (prerollChunksToSend && prerollChunksToSend.length) {
          const prerollRate = meta?.sampleRate || meta?.sampleRateHz || asrRate;
          const seq = pcmLastSeq;

          // Burst 1: Immediate (might be dropped by race condition)
          sendPrerollChunks(prerollChunksToSend, prerollRate, { turnId: currentTurnId, seq });
          // Burst 2: Delayed (guaranteed to arrive after server is armed)
          setTimeout(() => {
            if (currentTurnId) { // Only send if turn still active
              sendPrerollChunks(prerollChunksToSend, prerollRate, { turnId: currentTurnId, seq });
            }
          }, 50);

          prerollChunksToSend = null; // Clear so we don't triple-send below
        }
        try {
          logStage("client.deepgram_v3.turn_sequence_initiated", { turnId: currentTurnId });
        } catch (_) {}
      }
    }
    // Telemetry-only soft gate: we always send PCM when the hard gate allows it.
    maybeEmitSoftGateTelemetry({
      reason: softDecision.reason || "unknown",
      vadLikelySpeech: Boolean(softDecision.vadLikelySpeech),
      wsPhase,
      appPhase: phaseValue,
      wsReadyState,
    });

    const sr = meta?.sampleRate || meta?.sampleRateHz || null;
    const sampledBytes = chunk.byteLength || 0;
    const seq = Number.isFinite(meta.seq) ? Number(meta.seq) : pcmLastSeq;
    const metaSampleRate = Number(meta.sampleRate);
    if (Number.isFinite(metaSampleRate) && metaSampleRate > 0) {
      pcmSampleRate = metaSampleRate;
    }

    // Ensure this is declared as const
    const effectiveSampleRate =
      sr ||
      metaSampleRate ||
      (Number.isFinite(pcmSampleRate) && pcmSampleRate > 0 ? pcmSampleRate : asrRate);

    if (sampledBytes > 0 && ((Math.random() * 50) | 0) === 0) {
      try {
        logStage("client.pcm.send", {
          bytes: sampledBytes,
          samples: chunk.length || null,
          sampleRate: sr,
        });
      } catch (_) {}
    }
    if (!firstPcmToWsLogged && sampledBytes > 0 && !meta?.keepalive) {
      firstPcmToWsLogged = true;
      try {
        logStage("mic_debug.first_pcm_to_ws", {
          ts: typeof performance?.now === "function" ? performance.now() : Date.now(),
          bytes: sampledBytes,
          sampleRate: effectiveSampleRate,
          seq,
        });
      } catch (_) {}
    }

    // ✅ NEW: actually send the PCM over the WebSocket.
    const sent = safeSendAudioChunk(chunk, {
      lane: "mic",
      sampleRateHz: effectiveSampleRate,
      chunkCount,
      seq,
      keepalive: isKeepalive,
      turnId: currentTurnId,
    });

    if (!sent) {
      try {
        logStage("client.audio_chunk_send_failed", {
          seq,
          bytes: chunk.byteLength,
          chunkCount,
          sampleRate: effectiveSampleRate,
          turnId: currentTurnId,
        });
      } catch (_) {}
      recordPcmFrameOutcome({
        attempted: chunkCount,
        dropped: chunkCount,
        dropReason: lastPcmSendDropReason || "send_failed",
      });
      return;
    }

    const bytes = chunk.byteLength;
    pcmLastBytes = bytes;

    // Existing metrics + telemetry
    try {
      logStage("client.audio_chunk_send", { seq, bytes, batch_chunks: chunkCount, turnId: currentTurnId });
    } catch (_) {}

    if (typeof onClientAudioChunkSend === "function") {
      try {
        onClientAudioChunkSend({ seq, bytes, batch_chunks: chunkCount, ts_ms: Date.now() });
      } catch (err) {
        console.warn("onClientAudioChunkSend failed", err);
      }
    }

    const nextChunkTotal = getMicChunksValue() + chunkCount;
    const nextByteTotal = getMicBytesValue() + bytes;
    setMicChunksValue(nextChunkTotal);
    setMicBytesValue(nextByteTotal);

    recordPcmFrameOutcome({ attempted: chunkCount, sent: chunkCount });

    if (pcmSampleRate && Number.isFinite(pcmSampleRate)) {
      const samplesPerMs = pcmSampleRate / 1000;
      if (samplesPerMs > 0 && ((Math.random() * 50) | 0) === 0) {
        const ms_est = Math.round(chunk.length / samplesPerMs);
        try {
          logStage("client.pcm.flush", {
            samples: chunk.length,
            ms_est,
            ws_state: resolveSocket()?.readyState,
          });
        } catch (_) {}
      }
    }

    scheduleAudioKeepalive();
  }

  function setBaseEnabled(enabled, reason = "manual") {
    baseEnabled = Boolean(enabled);
    baseEnabledReason = reason || baseEnabledReason || "manual";
    updatePcmSenderState(`set_base_enabled:${reason}`);
  }

  function maybeAutoUnpauseSender(gateSnapshot) {
    if (pcmSenderAutoUnpauseGuard) {
      return false;
    }

    const {
      senderPaused,
      baseGate,
      hasStream,
      audioStreaming,
      captureAllowed,
      micPerm,
      wsReadyForAudio,
      fatalError,
      wsPhase,
    } = gateSnapshot;

    const onlyPauseBlockingSend = Boolean(
      senderPaused &&
      baseGate &&
      hasStream &&
      captureAllowed &&
      micPerm &&
      wsReadyForAudio &&
      !fatalError
    );

    if (!onlyPauseBlockingSend) {
      return false;
    }

    pcmSenderAutoUnpauseGuard = true;
    try {
      try {
        logStage("client.pcm_sender.auto_unpause", {
          wsPhase,
          baseGate,
          hasStream,
          audioStreaming,
          captureAllowed,
          micPerm,
          wsReadyForAudio,
          fatalError,
          senderPaused,
        });
      } catch (_) {}
      try {
        console.log("[ws_audio_runtime] pcm_sender.auto_unpause", gateSnapshot);
      } catch (_) {}
      try { setSenderPauseReason("greet", false); } catch (_) {}
      try { applySenderPausedState(); } catch (_) {}
      try { updatePcmSenderState("auto_unpause_watchdog"); } catch (_) {}
    } finally {
      pcmSenderAutoUnpauseGuard = false;
    }

    return true;
  }

  function computePcmGateSnapshot() {
    const AppState = getAppState();
    const stateSnapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
    const asrReady = Boolean(stateSnapshot?.asrReady);
    const micPerm = stateSnapshot && typeof stateSnapshot.micPermissionGranted === "boolean"
      ? stateSnapshot.micPermissionGranted
      : true;
    const fatalError = Boolean(gumFailed);
    const phaseValue = typeof stateSnapshot?.phase === "string" ? stateSnapshot.phase : null;
    const wsPhase = typeof stateSnapshot?.wsPhase === "string" ? stateSnapshot.wsPhase : null;
    const wsPhaseKnown = typeof wsPhase === "string" && wsPhase.length > 0;
    const wsReadyForAudio = wsPhaseKnown ? WS_READY_PHASES.has(wsPhase) : true;
    const audioStreaming = Boolean(isAudioStreaming());
    const senderPaused = Boolean(isSenderPaused());
    const captureAllowed = Boolean(canCaptureNow());
    const stream = captureStreamResolved || pcmSender?.mediaStream || null;
    const hasStream = Boolean(
      stream ||
      (pcmSender && typeof pcmSender.getStateSnapshot === "function" && pcmSender.getStateSnapshot()?.mediaStreamActive)
    );
    const baseGate = baseEnabled && hasStream;
    const gates = {
      asrReady,
      micPerm,
      senderPaused,
      canCapture: captureAllowed,
    };
    const shouldSendBase = Boolean(
      baseGate &&
      !gates.senderPaused &&
      gates.canCapture &&
      gates.micPerm &&
      wsReadyForAudio &&
      !fatalError
    );
    const shouldSend = FORCE_PCM_SEND
      ? (baseGate && gates.micPerm && !fatalError)
      : shouldSendBase;

    let decisionReason = "ok";
    if (!baseEnabled) {
      decisionReason = "base_disabled";
    } else if (!hasStream) {
      decisionReason = "no_stream";
    } else if (gates.senderPaused) {
      decisionReason = "sender_paused";
    } else if (!gates.canCapture) {
      decisionReason = "cannot_capture";
    } else if (!gates.micPerm) {
      decisionReason = "mic_perm";
    } else if (!wsReadyForAudio) {
      decisionReason = "ws_not_ready";
    } else if (fatalError) {
      decisionReason = "fatal_error";
    } else if (FORCE_PCM_SEND && !shouldSendBase) {
      decisionReason = "forced";
    }

    return {
      sid: stateSnapshot?.sid || stateSnapshot?.sessionId || null,
      phaseValue,
      wsPhase,
      wsPhaseKnown,
      wsReadyForAudio,
      audioStreaming,
      senderPaused,
      captureAllowed,
      hasStream,
      baseGate,
      shouldSendBase,
      shouldSend,
      asrReady,
      micPerm,
      fatalError,
      decisionReason,
      trackState: stream?.getAudioTracks?.()[0]?.readyState || "unknown",
      ctxState: audioCtx?.state || "unknown",
      isAudioStreaming: audioStreaming,
    };
  }

  function getPcmSenderGateSnapshot() {
    return computePcmGateSnapshot();
  }

  function updatePcmSenderState(reason = "unknown") {
    const gateSnapshot = computePcmGateSnapshot();
    const {
      sid,
      phaseValue,
      wsPhase,
      wsPhaseKnown,
      wsReadyForAudio,
      audioStreaming,
      senderPaused,
      captureAllowed,
      hasStream,
      baseGate,
      shouldSendBase,
      shouldSend,
      asrReady,
      micPerm,
      fatalError,
      decisionReason,
      trackState,
      ctxState,
    } = gateSnapshot;
    const socket = resolveSocket();
    const wsReadyState = socket?.readyState ?? null;
    const hardGate = computeHardGateSnapshot({
      wsPhase,
      fatalError,
      wsReadyState,
    });
    if (maybeAutoUnpauseSender(gateSnapshot)) {
      return;
    }
    const previousState = pcmSenderStateLast;
    const gates = {
      asrReady,
      micPerm,
      senderPaused,
      canCapture: captureAllowed,
    };
    const gateSignature = [
      phaseValue || "null",
      wsPhase || "null",
      baseGate ? "B1" : "B0",
      hasStream ? "S1" : "S0",
      shouldSend ? "E1" : "E0",
      decisionReason || "null",
    ].join("|");
    const signatureChanged = gateSignature !== lastPcmGateLoggedSignature;

    if (signatureChanged) {
      lastPcmGateLoggedSignature = gateSignature;
      emitHardGateSnapshot({
        hardGate,
        gateSnapshot,
        reason: "gate_change",
      });
    }

    try {
      logStage("client.pcm_sender_state.update", {
        gumFailed,
        isAudioStreaming: audioStreaming,
        canCaptureNow: captureAllowed,
        senderPaused,
        trackReady: trackState,
        ctxState,
        sid: sid || null,
      });
    } catch (_) {}

    try {
      console.log("AskChip pcm_sender.gates", { reason, ...gates });
    } catch (_) {}

    if (signatureChanged) {
      try {
        logStage("client.audio_stream_state_inputs", {
          reason,
          shouldSend,
          base_enabled: baseGate,
          hasStream,
          force_pcm_send: FORCE_PCM_SEND,
          isAudioStreaming: audioStreaming,
          senderPaused: gates.senderPaused,
          canCaptureNow: gates.canCapture,
          asrReady: gates.asrReady,
          micPerm: gates.micPerm,
          fatalError,
          hasPcmSender: !!pcmSender,
          baseEnabledReason,
          phase: phaseValue,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
      try {
        console.log("[ws_audio_runtime] updatePcmSenderState.inputs", {
          reason,
          shouldSend,
          base_enabled: baseGate,
          hasStream,
          force_pcm_send: FORCE_PCM_SEND,
          isAudioStreaming: audioStreaming,
          senderPaused: gates.senderPaused,
          canCaptureNow: gates.canCapture,
          asrReady: gates.asrReady,
          micPerm: gates.micPerm,
          fatalError,
          hasPcmSender: !!pcmSender,
          baseEnabledReason,
          phase: phaseValue,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
    }

    const socketReady = socket
      ? (typeof WebSocket !== "undefined"
        ? socket.readyState === WebSocket.OPEN
        : socket.readyState === 1)
      : false;

    if (signatureChanged) {
      const summary = {
        reason,
        phase: phaseValue,
        hasStream,
        wsConnected: socketReady,
        senderPaused,
        vadGateOpen: captureAllowed,
        wsPhase,
        wsPhaseKnown,
        wsReadyForAudio,
        shouldSend,
        base_enabled: baseGate,
        enabled: shouldSend,
        isAudioStreaming: audioStreaming,
        baseEnabledReason,
        fatalError,
      };

      try {
        logStage("client.audio_stream_state_summary", summary);
      } catch (_) {}
      try {
        console.log("[ws_audio_runtime] pcm_sender_state_summary", summary);
      } catch (_) {}
    }

    if (!pcmSender || typeof pcmSender.setEnabled !== "function") {
      logSenderDecision({
        reason,
        decisionReason,
        shouldSend,
        previous_state: previousState,
        next_state: previousState,
        phase: phaseValue,
        wsPhase,
        ws_ready: wsReadyForAudio,
        ws_ready_state: socket?.readyState ?? null,
        isAudioStreaming: audioStreaming,
        senderPaused,
        canCaptureNow: captureAllowed,
        firstChunkSeen: readFirstChunkSeen(),
        hasCaptureStream: Boolean(captureStreamResolved || captureStreamProvider),
        vadState: typeof getVadController === "function" ? getVadController()?.getState?.() || null : null,
        chunkIndex: Number.isFinite(pcmLastSeq) ? pcmLastSeq : null,
        chunkBytes: Number.isFinite(pcmLastBytes) ? pcmLastBytes : null,
        ts_ms: Date.now(),
        ts_ms_monotonic: typeof performance !== "undefined" && typeof performance.now === "function"
          ? performance.now()
          : null,
      });
      return;
    }

    if (previousState !== shouldSend) {
      try {
        logStage("client.audio_stream_state", {
          reason,
          enabled: shouldSend,
          base_enabled: shouldSendBase,
          force_pcm_send: FORCE_PCM_SEND,
          isAudioStreaming: audioStreaming,
          senderPaused,
          canCaptureNow: captureAllowed,
          asrReady,
          fatalError,
          phase: phaseValue,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
      try {
        recordClientBannerEvent("pcm_sender_state_summary", {
          reason,
          enabled: shouldSend,
          base_enabled: shouldSendBase,
          force_pcm_send: FORCE_PCM_SEND,
          gates: {
            asrReady: gates.asrReady,
            micPerm: gates.micPerm,
            senderPaused: gates.senderPaused,
            canCapture: gates.canCapture,
            fatalError,
          },
          hasStream,
          phase: phaseValue,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
      try {
        console.log("[ws_audio_runtime] updatePcmSenderState", {
          reason,
          enabled: shouldSend,
          base_enabled: shouldSendBase,
          force_pcm_send: FORCE_PCM_SEND,
          isAudioStreaming: audioStreaming,
          senderPaused,
          canCaptureNow: captureAllowed,
          asrReady,
          fatalError,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
      const gateOpened = previousState !== true && shouldSend === true;
      if (gateOpened) {
        const appState = getAppState();
        const gateSid = sid || appState?.sid || appState?.sessionId || null;
        try {
          logStage("client.deepgram_v3.sender_gate_opened", {
            sid: gateSid,
            phase: phaseValue,
            wsPhase,
            baseEnabled,
            hasStream,
            fatalError,
            asrReady,
            isAudioStreaming: audioStreaming,
            ctxState,
            decisionReason,
          });
        } catch (_) {}
      }
      pcmSenderStateLast = shouldSend;
      try {
        console.log("client.pcm_sender.state", {
          enabled: shouldSend,
          reason,
          signatureChanged,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch {}
      try {
        logStage("client.pcm_sender.state", {
          enabled: shouldSend,
          reason,
          signatureChanged,
          senderPaused,
          asrReady,
          wsPhase,
          wsPhaseKnown,
          wsReady: wsReadyForAudio,
          fatalError,
        });
      } catch (_) {}
    }

    logSenderDecision({
      reason,
      decisionReason,
      shouldSend,
      previous_state: previousState,
      next_state: shouldSend,
      phase: phaseValue,
      wsPhase,
      ws_ready: wsReadyForAudio,
      ws_ready_state: socket?.readyState ?? null,
      isAudioStreaming: audioStreaming,
      senderPaused,
      canCaptureNow: captureAllowed,
      fatalError,
      firstChunkSeen: readFirstChunkSeen(),
      hasCaptureStream: Boolean(captureStreamResolved || captureStreamProvider),
      vadState: typeof getVadController === "function" ? getVadController()?.getState?.() || null : null,
      chunkIndex: Number.isFinite(pcmLastSeq) ? pcmLastSeq : null,
      chunkBytes: Number.isFinite(pcmLastBytes) ? pcmLastBytes : null,
      ts_ms: Date.now(),
      ts_ms_monotonic: typeof performance !== "undefined" && typeof performance.now === "function"
        ? performance.now()
        : null,
    });

    pcmSender.setEnabled(shouldSend);
  }

  async function ensurePcmSender() {
    if (pcmSender) {
      return pcmSender;
    }
    if (pcmSenderInitPromise) {
      return pcmSenderInitPromise;
    }
    if (typeof initPcmSender !== "function") {
      throw new Error("initPcmSender not provided");
    }
    let stream = null;
    if (captureStreamProvider) {
      stream = await captureStreamProvider();
      if (!stream) {
        markGumFailed("no_stream_returned");
        return null;
      }
      if (!stream.getAudioTracks || stream.getAudioTracks().length === 0) {
        markGumFailed("no_audio_tracks");
        return null;
      }
      captureStreamResolved = stream;
      const track = stream.getAudioTracks()[0];
      lastTrackState = track?.readyState || lastTrackState;
      if (track?.readyState === "ended") {
        markGumFailed("track_ended_immediately", { trackState: track.readyState });
        return null;
      }
      if (track) {
        try {
          track.onended = () => {
            scheduleMicReacquire("track_ended", 800);
          };
        } catch (_) {}
      }
      updatePcmSenderState("ensure_sender_stream_resolved");
    }
    audioCtx = audioCtx || getMicAudioContext();
    if (audioCtx?.state === "suspended" && typeof audioCtx.resume === "function") {
      try {
        await audioCtx.resume();
      } catch (err) {
        logStage("client.audio_context.resume_failed", { err: String(err) });
      }
    }

    pcmSenderInitPromise = initPcmSender(stream, {
      audioCtx,
      onSampleRate: handleSampleRate,
      onFrame: handlePcmFrame,
      onSend: handlePcmSend,
      onError: handlePcmError,
      chunkMs: PCM_TARGET_BATCH_MS,
      flushIntervalMs: PCM_FLUSH_TIMER_MS,
    }).then(async (sender) => {
      pcmSender = sender;
      try {
        if (audioCtx?.state === "suspended") {
          logStage("client.audio_context.resume_attempt", {});
          wsDiag("audio_warmup", { state: audioCtx?.state });
          await audioCtx.resume();
          logStage("client.audio_context.resumed", {});
        }
      } catch (err) {
        logStage("client.audio_context.resume_failed", { err: String(err) });
      }
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

  function getPcmRing() {
    return ringBufferManager.getRing();
  }

  function getPcmSenderSnapshot() {
    const sender = pcmSender || null;
    const snapshot = sender && typeof sender.getStateSnapshot === "function"
      ? sender.getStateSnapshot()
      : null;
    const stream = sender?.mediaStream || null;
    const tracks = stream?.getAudioTracks?.() || [];
    const trackStates = snapshot?.tracks || tracks.map((track) => ({
      id: track?.id || null,
      kind: track?.kind || null,
      label: track?.label || null,
      enabled: Boolean(track?.enabled),
      muted: Boolean(track?.muted),
      readyState: track?.readyState || null,
    }));
    return {
      hasSender: Boolean(sender),
      mediaStreamActive: snapshot?.mediaStreamActive ?? Boolean(stream?.active),
      mediaStreamId: snapshot?.mediaStreamId || stream?.id || null,
      audioContextState: snapshot?.audioContextState || sender?.audioContext?.state || null,
      senderEnabled: snapshot?.enabled ?? null,
      trackCount: Array.isArray(trackStates) ? trackStates.length : 0,
      tracks: trackStates,
      lastChunkAt: micLastChunkAt || null,
    };
  }

  function recordRecorderChunkPublic(tsMs) {
    recordRecorderChunk(tsMs);
  }

  function resetPcmStateForTesting() {
    pcmSender = null;
    pcmSenderInitPromise = null;
    pcmLastSeq = 0;
    pcmLastBytes = null;
    pcmSampleRate = asrRate;
    pcmHardwareSampleRate = null;
    baseEnabled = false;
    baseEnabledReason = "reset";
    resetSilenceSuppression();
    clearAudioKeepaliveTimer();
    micLastChunkAt = 0;
    lastRealAudioAt = 0;
  }

  function setCaptureStreamProvider(fn) {
    captureStreamProvider = typeof fn === "function" ? fn : null;
    if (typeof fn === "function") {
      try {
        const maybeStream = fn();
        if (maybeStream && typeof maybeStream.then === "function") {
          maybeStream
            .then((stream) => {
              captureStreamResolved = stream || captureStreamResolved;
              updatePcmSenderState("capture_stream_resolved");
            })
            .catch(() => {});
        } else {
          captureStreamResolved = maybeStream || captureStreamResolved;
        }
      } catch (_) {}
    }
    updatePcmSenderState("capture_stream_provider_set");
  }

  function setAudioKeepaliveMs(value) {
    const next = Number.isFinite(value) && value > 0 ? value : AUDIO_KEEPALIVE_MS;
    audioKeepaliveMs = next;
    scheduleAudioKeepalive();
  }

  function setAudioKeepaliveIdleMs(value) {
    const next = Number.isFinite(value) && value >= 0 ? value : AUDIO_KEEPALIVE_IDLE_MS;
    audioKeepaliveIdleMs = next;
  }

  return {
    ensurePcmSender,
    handlePcmFrame,
    handlePcmSend,
    handleSampleRate,
    primeAsrStreamFromRing,
    recordRecorderChunk: recordRecorderChunkPublic,
    getPcmRing,
    getPcmSenderSnapshot,
    getPcmSenderGateSnapshot,
    resetPcmStateForTesting,
    setCaptureStreamProvider,
    setBaseEnabled,
    resetSilenceSuppression,
    resetTurnForNextUser,
    updatePcmSenderState,
    scheduleAudioKeepalive,
    clearAudioKeepaliveTimer,
    sendAudioKeepaliveNow,
    setAudioKeepaliveMs,
    setAudioKeepaliveIdleMs,
    getAudioContext: () => audioCtx,
    getPcmWarm: () => pcmWarm,
    resetFirstChunkTelemetry,
  };
}

// Ensure global exposure for loader checks
if (typeof window !== "undefined") {
  window.createWsAudioRuntime = createWsAudioRuntime;
}
