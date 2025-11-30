// app/static/js/audio/ws_audio_runtime.js
// Encapsulates PCM ring buffer, PCM sender, and ASR priming helpers.

import { isTypedArray, toArrayBuffer } from "../utils/binary.js";
import { recordClientBannerEvent } from "../ws/telemetry.js";
import { logStage } from "../ws/telemetry.js";
import { PHASE as VOICE_PHASE } from "../voice/phase_controller.js";
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
const DEFAULT_PCM_CHANNELS = 1;
const PCM_TARGET_BATCH_MS = 60;
const PCM_FLUSH_TIMER_MS = 50;
const SILENCE_FRAME_MS = 20;
const SILENCE_REQUIRED_FRAMES = 5;
const SILENCE_RMS_THRESHOLD = 0.012;
const SILENCE_PREROLL_MS = 100;
const SILENCE_IDLE_TICK_MS = 5000;
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

  clear() {
    this.write = 0;
    this.filled = false;
  }
}

function getWindowPcmRing(sampleRate) {
  if (typeof window === "undefined") {
    return null;
  }
  if (!(window.__pcmRing instanceof PcmRingBuffer)) {
    window.__pcmRing = new PcmRingBuffer({
      millis: DEFAULT_RING_CAPACITY_MS,
      sampleRate,
      channels: DEFAULT_PCM_CHANNELS,
    });
  }
  return window.__pcmRing;
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
  let lastTrackState = null;
  let lastConstraints = null;
  let reacquireAttempted = false;
  let firstPcmToWsLogged = false;

  // Global diagnostics for debugging
  if (typeof window !== "undefined") {
    try {
      window.__gumFailed = gumFailed;
      window.__askchipAudioDiag = {
        get gumFailed() { return gumFailed; },
        get lastGumError() { return lastGumError; },
        get lastTrackState() { return lastTrackState; },
        get lastConstraints() { return lastConstraints; },
      };
    } catch (_) {}
  }

  function markGumFailed(reason, detail = {}) {
    gumFailed = true;
    lastGumError = detail?.error || detail?.message || null;
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
    if (!reacquireAttempted) {
      reacquireAttempted = true;
      micReacquire(reason);
    }
  }

  async function micReacquire(reason = "gum_failed") {
    try {
      logStage("client.mic.reacquire.start", { reason });
    } catch (_) {}

    gumFailed = false;
    lastGumError = null;
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
    try {
      const ensureHardware = typeof window !== "undefined" ? window.ensureMicHardware : null;
      if (typeof ensureHardware === "function") {
        newStream = await ensureHardware(lastConstraints);
      }
      if (!newStream) {
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
        markGumFailed("track_ended_immediately_reacquire", {
          trackState: track?.readyState || null,
          constraints: lastConstraints,
        });
        return null;
      }
    } catch (err) {
      gumFailed = true;
      lastGumError = err?.message || String(err);
      logStage("client.mic.reacquire.failed", { err: String(err) });
      return null;
    }

    captureStreamResolved = newStream;
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

    reacquireAttempted = false;

    return newStream;
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

  function logAudioGate(label, detail = {}) {
    if (!PCM_SENDER_DEBUG) {
      return;
    }
    try {
      logStage(label, detail);
    } catch (_) {
      // Do not let logging break the audio path
    }
  }

  function logWsAudioDropOnce(meta, reason) {
    wsAudioDropCount += 1;
    if (wsAudioDropCount <= WS_AUDIO_DROP_LOG_LIMIT) {
      try {
        logStage("client.ws.audio_drop", {
          lane: meta?.lane || "mic",
          reqId: meta?.reqId || null,
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
    const phase = getAppState()?.phase || null;
    if (phase === VOICE_PHASE.Greet) {
      try {
        logStage?.("client.mic.start_blocked", {
          reason: "greet_phase",
          source: "pcm_send",
        });
      } catch (_) {}
      return false;
    }
    const currentReqId = typeof getCurrentTurnReqId === "function"
      ? (getCurrentTurnReqId() || null)
      : null;
    const enrichedMeta = meta && typeof meta === "object" ? { ...meta } : {};
    const wsPhase = getAppState()?.wsPhase || null;
    try {
      logStage("client.audio_chunk_attempt", {
        length: payload?.byteLength || payload?.length || null,
        ws_ready: resolveSocket()?.readyState,
      });
    } catch (_) {}
    if (!currentReqId) {
      if (!warnedMissingReqId) {
        console.warn("ws_audio_runtime: audio chunk without active req_id; dropping");
        warnedMissingReqId = true;
      }
      try {
        logStage("client.audio_chunk_dropped", {
          lane: enrichedMeta.lane || "mic",
          reqId: null,
          reason: "missing_reqId",
        });
      } catch (_) {}
      return false;
    } else {
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
            sent: true,
          });
        } catch (_) {}
        return true;
      } catch (err) {
        console.warn("sendAudioChunk delegate failed", err);
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
          });
        } catch (_) {}
        return true;
      } catch (err) {
        console.warn("WSClient.sendAudioChunk failed", err);
        logWsAudioDropOnce(enrichedMeta, "send_exception");
      }
    }
    try {
      logStage("client.audio_chunk_send_failed", {
        lane: enrichedMeta.lane || "mic",
        reqId: enrichedMeta.reqId || null,
        keepalive: !!enrichedMeta.keepalive,
        sampleRate: enrichedMeta.sampleRateHz || enrichedMeta.sampleRate || null,
      });
    } catch (_) {}
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
  const asrRate = Number.isFinite(initialAppState?.targetSampleRate)
    ? Number(initialAppState.targetSampleRate)
    : PCM_TARGET_SAMPLE_RATE;

  let pcmRing = getWindowPcmRing(asrRate);
  if (!pcmRing) {
    pcmRing = new PcmRingBuffer({
      millis: DEFAULT_RING_CAPACITY_MS,
      sampleRate: asrRate,
      channels: DEFAULT_PCM_CHANNELS,
    });
  }

  let pcmSender = null;
  let pcmSenderInitPromise = null;
  let pcmLastSeq = 0;
  let pcmLastBytes = null;
  let pcmSampleRate = asrRate;
  let pcmHardwareSampleRate = null;
  let audioCtx = null;
  let baseEnabled = false;
  let baseEnabledReason = "boot";
  let silenceConsecutiveFrames = 0;
  let silenceSuppressed = false;
  let silenceLastIdleTickAt = 0;
  let micKeepaliveTimerId = null;
  let micLastChunkAt = 0;
  let lastRealAudioAt = 0;
  localFirstChunkSeen = readFirstChunkSeen();
  localMicRecordingStartAt = readMicRecordingStartAt();
  audioKeepaliveMs = Number.isFinite(initialAudioKeepaliveMs) && initialAudioKeepaliveMs > 0
    ? initialAudioKeepaliveMs
    : AUDIO_KEEPALIVE_MS;

  setInterval(() => {
    const track = getCaptureStream?.()?.getAudioTracks?.()[0];
    try {
      logStage("client.mic.heartbeat", {
        trackReadyState: track?.readyState || "missing",
        enabled: track?.enabled,
        muted: track?.muted,
        audioCtxState: audioCtx?.state || "unknown",
        isInterrupted: audioCtx?.state === "interrupted",
        gumFailed,
      });
      if (audioCtx?.state === "interrupted") {
        try {
          audioCtx.resume();
          logStage("client.audio_context.recovered_from_interrupted", {});
        } catch (_) {}
      }
    } catch (_) {}
  }, 500);

  function clearAudioKeepaliveTimer() {
    if (micKeepaliveTimerId) {
      clearTimeout(micKeepaliveTimerId);
      micKeepaliveTimerId = null;
    }
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
    const ws = resolveSocket();
    if (!ws) {
      return;
    }
    if (typeof WebSocket !== "undefined" && ws.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      if (safeSendJSON({ type: "client.idle", lane: "mic", ts: now })) {
        silenceLastIdleTickAt = now;
      }
    } catch (err) {
      console.warn("client.idle send failed", err);
    }
  }

  function evaluateSilenceSuppression(int16, sampleRate, now) {
    if (!(int16 instanceof Int16Array) || !int16.length) {
      return false;
    }
    const rate = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : asrRate;
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
    const rate = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : asrRate;
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
      const sr = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : asrRate;
      if (!safeSendAudioChunk(payload, { lane: "mic" })) {
        console.warn("preroll send fallback failed");
      } else {
        handlePcmSend(payload, { chunkCount: 1, sampleRate: sr });
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

    let resumeTriggered = false;
    let prerollChunks = [];
    if (isAudioStreaming()) {
      resumeTriggered = evaluateSilenceSuppression(wire, currentSampleRate, now);
      if (resumeTriggered) {
        prerollChunks = getSilencePreroll(currentSampleRate);
      }
    }

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
      if (typeof pcmRing?.push === "function") {
        pcmRing.push(wire);
      }
    } catch (e) {
      console.warn("pcmRing.push failed", e);
    }

    if (!isAudioStreaming()) {
      return;
    }

    if (resumeTriggered) {
      sendPrerollAndChunk(prerollChunks, wire, currentSampleRate);
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
    wsDiag("pcm_send_attempt", {
      bytes: chunk?.byteLength,
      gumFailed,
      audioCtxState: audioCtx?.state,
    });
    try {
      logStage("client.audio_chunk", {
        bytes: chunk?.byteLength || 0,
        gumFailed,
      });
    } catch (_) {}
    // Instead of hard abort, allow one retry per session.
    if (gumFailed) {
      try {
        logStage("client.mic.capture_retry_due_to_gum_failed", {
          gumFailed,
          lastGumError,
          lastTrackState,
          lastConstraints,
        });
      } catch (_) {}
      gumFailed = false;
      if (typeof window !== "undefined") {
        try { window.__gumFailed = gumFailed; } catch (_) {}
      }
    }
    const AppState = getAppState();
    const ws = resolveSocket();
    const wsReadyState = ws?.readyState ?? null;
    const wsPhase = typeof AppState?.wsPhase === "string" ? AppState.wsPhase : null;
    const wsPhaseKnown = typeof wsPhase === "string" && wsPhase.length > 0;
    const wsReadyPhase = wsPhaseKnown ? WS_READY_PHASES.has(wsPhase) : true;
    const phaseValue = typeof AppState?.phase === "string" ? AppState.phase : null;
    const senderPaused = Boolean(isSenderPaused());
    const captureAllowed = Boolean(canCaptureNow());

    if (wsPhaseKnown && !wsReadyPhase) {
      logAudioGate("client.audio.gate", {
        action: "send_chunk",
        blocked: true,
        reason: "ws_phase_not_ready",
        phase: phaseValue,
        wsPhase,
        readyPhases: Array.from(WS_READY_PHASES),
        wsReadyState,
        senderPaused,
        canCaptureNow: captureAllowed,
      });
      return;
    }

    if (typeof WebSocket !== "undefined" && ws && ws.readyState !== WebSocket.OPEN) {
      logAudioGate("client.audio.gate", {
        action: "send_chunk",
        blocked: true,
        reason: "ws_closed",
        phase: phaseValue,
        wsPhase,
        readyPhases: Array.from(WS_READY_PHASES),
        wsReadyState,
        senderPaused,
        canCaptureNow: captureAllowed,
      });
      return;
    }

    if (!isAudioStreaming()) {
      logAudioGate("client.audio.gate", {
        action: "send_chunk",
        blocked: true,
        reason: "not_streaming",
        phase: phaseValue,
        wsPhase,
        readyPhases: Array.from(WS_READY_PHASES),
        wsReadyState,
        senderPaused,
        canCaptureNow: captureAllowed,
      });
      return;
    }

    logAudioGate("client.audio.gate", {
      action: "send_chunk",
      blocked: false,
      reason: "ok",
      phase: phaseValue,
      wsPhase,
      wsReadyState,
    });
    const sr = meta?.sampleRate || meta?.sampleRateHz || null;
    const sampledBytes = chunk.byteLength || 0;
    const chunkCount = Number.isFinite(meta.chunkCount) ? Number(meta.chunkCount) : 1;
    const seq = Number.isFinite(meta.seq) ? Number(meta.seq) : pcmLastSeq;
    const metaSampleRate = Number(meta.sampleRate);
    if (Number.isFinite(metaSampleRate) && metaSampleRate > 0) {
      pcmSampleRate = metaSampleRate;
    }

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
    });

    if (!sent) {
      try {
        logStage("client.audio_chunk_send_failed", {
          seq,
          bytes: chunk.byteLength,
          chunkCount,
          sampleRate: effectiveSampleRate,
        });
      } catch (_) {}
      return;
    }

    const bytes = chunk.byteLength;
    pcmLastBytes = bytes;

    // Existing metrics + telemetry
    try {
      logStage("client.audio_chunk_send", { seq, bytes, batch_chunks: chunkCount });
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

  function updatePcmSenderState(reason = "unknown") {
    const AppState = getAppState();
    const stateSnapshot = typeof AppState?.getState === "function" ? AppState.getState() : AppState;
    const asrReady = Boolean(stateSnapshot?.asrReady);
    const ttsActive = Boolean(stateSnapshot?.ttsActive);
    const micPerm = stateSnapshot && typeof stateSnapshot.micPermissionGranted === "boolean"
      ? stateSnapshot.micPermissionGranted
      : true;
    const turnActive = Object.prototype.hasOwnProperty.call(stateSnapshot || {}, "turnActive")
      ? Boolean(stateSnapshot.turnActive)
      : true;
    const phaseValue = typeof stateSnapshot?.phase === "string" ? stateSnapshot.phase : null;
    const phaseAllowsSend = phaseValue
      ? phaseValue === "conversation" ||
        phaseValue === VOICE_PHASE.ConversationReady ||
        phaseValue === VOICE_PHASE.UserTurn
      : true;
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
    try {
      logStage("client.pcm_sender_state.update", {
        gumFailed,
        isAudioStreaming: isAudioStreaming(),
        canCaptureNow: canCaptureNow(),
        senderPaused: isSenderPaused(),
        trackReady: stream?.getAudioTracks?.()[0]?.readyState || "unknown",
        ctxState: audioCtx?.state || "unknown",
      });
    } catch (_) {}
    const baseGate = baseEnabled && hasStream;
    const gates = {
      asrReady,
      ttsActive,
      micPerm,
      senderPaused,
      canCapture: captureAllowed,
    };
    const shouldSendBase = Boolean(
      baseGate &&
      audioStreaming &&
      !gates.senderPaused &&
      gates.canCapture &&
      gates.asrReady &&
      !gates.ttsActive &&
      gates.micPerm &&
      turnActive &&
      phaseAllowsSend &&
      wsReadyForAudio
    );
    const shouldSend = FORCE_PCM_SEND
      ? gates.asrReady && !gates.ttsActive && gates.micPerm
      : shouldSendBase;
    const previousState = pcmSenderStateLast;

    let decisionReason = "ok";
    if (!baseEnabled) {
      decisionReason = "base_disabled";
    } else if (!hasStream) {
      decisionReason = "no_stream";
    } else if (!audioStreaming) {
      decisionReason = "not_streaming";
    } else if (gates.senderPaused) {
      decisionReason = "sender_paused";
    } else if (!gates.canCapture) {
      decisionReason = "cannot_capture";
    } else if (!gates.asrReady) {
      decisionReason = "asr_not_ready";
    } else if (gates.ttsActive) {
      decisionReason = "tts_active";
    } else if (!gates.micPerm) {
      decisionReason = "mic_perm";
    } else if (!turnActive) {
      decisionReason = "turn_inactive";
    } else if (!phaseAllowsSend) {
      decisionReason = "phase_not_ready";
    } else if (!wsReadyForAudio) {
      decisionReason = "ws_not_ready";
    } else if (FORCE_PCM_SEND && !shouldSendBase) {
      decisionReason = "forced";
    }

    try {
      console.log("AskChip pcm_sender.gates", { reason, ...gates });
    } catch (_) {}

    // Always log the raw inputs on every call
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
        ttsActive: gates.ttsActive,
        micPerm: gates.micPerm,
        turnActive,
        hasPcmSender: !!pcmSender,
        baseEnabledReason,
        phase: phaseValue,
        phase_allows_send: phaseAllowsSend,
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
        ttsActive: gates.ttsActive,
        micPerm: gates.micPerm,
        turnActive,
        hasPcmSender: !!pcmSender,
        baseEnabledReason,
        phase: phaseValue,
        phase_allows_send: phaseAllowsSend,
        wsPhase,
        wsPhaseKnown,
        ws_ready: wsReadyForAudio,
      });
    } catch (_) {}

    const socket = resolveSocket();
    const socketReady = socket
      ? (typeof WebSocket !== "undefined"
        ? socket.readyState === WebSocket.OPEN
        : socket.readyState === 1)
      : false;

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
    };

    try {
      logStage("client.audio_stream_state_summary", summary);
    } catch (_) {}
    try {
      console.log("[ws_audio_runtime] pcm_sender_state_summary", summary);
    } catch (_) {}

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
          turnActive,
          phase: phaseValue,
          phase_allows_send: phaseAllowsSend,
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
            ttsActive: gates.ttsActive,
            micPerm: gates.micPerm,
            senderPaused: gates.senderPaused,
            canCapture: gates.canCapture,
          },
          hasStream,
          turnActive,
          phase: phaseValue,
          phase_allows_send: phaseAllowsSend,
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
          turnActive,
          phase_allows_send: phaseAllowsSend,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch (_) {}
      pcmSenderStateLast = shouldSend;
      try {
        console.log("client.pcm_sender.state", {
          enabled: shouldSend,
          reason,
          wsPhase,
          wsPhaseKnown,
          ws_ready: wsReadyForAudio,
        });
      } catch {}
      try {
        logStage("client.pcm_sender.state", {
          enabled: shouldSend,
          reason,
          senderPaused,
          asrReady,
          wsPhase,
          wsPhaseKnown,
          wsReady: wsReadyForAudio,
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
    return pcmRing;
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
    resetPcmStateForTesting,
    setCaptureStreamProvider,
    setBaseEnabled,
    resetSilenceSuppression,
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
