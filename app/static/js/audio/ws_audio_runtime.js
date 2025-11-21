// app/static/js/audio/ws_audio_runtime.js
// Encapsulates PCM ring buffer, PCM sender, and ASR priming helpers.

import { isTypedArray, toArrayBuffer } from "../utils/binary.js";

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
let __firstPcmFrameLogged = false;

const primedSessionIds = new Set();

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
    AppState = {},
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

  __firstPcmFrameLogged = false;

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

  const writeMicRecordingStartAt = (value) => {
    if (typeof setMicRecordingStartAt === "function") {
      try {
        setMicRecordingStartAt(value);
      } catch (err) {
        console.warn("setMicRecordingStartAt failed", err);
      }
    }
    localMicRecordingStartAt = value;
  };

  const safeSendAudioChunk = (payload, meta = {}) => {
    const currentReqId = typeof getCurrentTurnReqId === "function"
      ? (getCurrentTurnReqId() || null)
      : null;
    const enrichedMeta = meta && typeof meta === "object" ? { ...meta } : {};
    if (!currentReqId) {
      if (!warnedMissingReqId) {
        console.warn("ws_audio_runtime: audio chunk without active req_id; sending anyway");
        warnedMissingReqId = true;
      }
    } else {
      warnedMissingReqId = false;
      if (!enrichedMeta.reqId) {
        enrichedMeta.reqId = currentReqId;
      }
    }
    if (!enrichedMeta.sampleRateHz && typeof enrichedMeta.sampleRate === "number") {
      enrichedMeta.sampleRateHz = enrichedMeta.sampleRate;
    }
    if (typeof sendAudioChunk === "function") {
      try {
        sendAudioChunk(payload, enrichedMeta);
        try {
          logStage("client.audio_chunk_send", {
            lane: enrichedMeta.lane || "mic",
            reqId: enrichedMeta.reqId || null,
            keepalive: !!enrichedMeta.keepalive,
            sampleRate: enrichedMeta.sampleRateHz || enrichedMeta.sampleRate || null,
            source: "delegate",
          });
        } catch (_) {}
        return true;
      } catch (err) {
        console.warn("sendAudioChunk delegate failed", err);
      }
    }
    const wsClient = resolveWsClient();
    if (wsClient && typeof wsClient.sendAudioChunk === "function") {
      try {
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
    } else if (AppState && typeof AppState === "object") {
      AppState.micRms = rms;
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

  const asrRate = Number.isFinite(AppState?.targetSampleRate)
    ? Number(AppState.targetSampleRate)
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
  let pcmSampleRate = asrRate;
  let pcmHardwareSampleRate = null;
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
    const sr = meta?.sampleRate || meta?.sampleRateHz || null;
    const sampledBytes = chunk.byteLength || 0;
    if (sampledBytes > 0 && ((Math.random() * 50) | 0) === 0) {
      try {
        logStage("client.pcm.send", {
          bytes: sampledBytes,
          samples: chunk.length || null,
          sampleRate: sr,
        });
      } catch (_) {}
    }
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

  function updatePcmSenderState(reason = "unknown") {
    const asrReady = Boolean(AppState?.asrReady);
    const turnActive = Object.prototype.hasOwnProperty.call(AppState || {}, "turnActive")
      ? Boolean(AppState.turnActive)
      : true;
    const audioStreaming = Boolean(isAudioStreaming());
    const senderPaused = Boolean(isSenderPaused());
    const captureAllowed = Boolean(canCaptureNow());
    const shouldSendBase = Boolean(
      audioStreaming &&
      !senderPaused &&
      captureAllowed &&
      asrReady &&
      turnActive
    );
    const shouldSend = FORCE_PCM_SEND ? asrReady : shouldSendBase;

    // Always log the raw inputs on every call
    try {
      logStage("client.audio_stream_state_inputs", {
        reason,
        shouldSend,
        base_enabled: shouldSendBase,
        force_pcm_send: FORCE_PCM_SEND,
        isAudioStreaming: audioStreaming,
        senderPaused,
        canCaptureNow: captureAllowed,
        asrReady,
        turnActive,
        hasPcmSender: !!pcmSender,
      });
    } catch (_) {}
    try {
      console.log("[ws_audio_runtime] updatePcmSenderState.inputs", {
        reason,
        shouldSend,
        base_enabled: shouldSendBase,
        force_pcm_send: FORCE_PCM_SEND,
        isAudioStreaming: audioStreaming,
        senderPaused,
        canCaptureNow: captureAllowed,
        asrReady,
        turnActive,
        hasPcmSender: !!pcmSender,
      });
    } catch (_) {}

    if (!pcmSender || typeof pcmSender.setEnabled !== "function") {
      return;
    }

    if (pcmSenderStateLast !== shouldSend) {
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
        });
      } catch (_) {}
      pcmSenderStateLast = shouldSend;
    }

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
    const stream = captureStreamProvider ? await captureStreamProvider() : null;
    pcmSenderInitPromise = initPcmSender(stream, {
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
    pcmSampleRate = asrRate;
    pcmHardwareSampleRate = null;
    resetSilenceSuppression();
    clearAudioKeepaliveTimer();
    micLastChunkAt = 0;
    lastRealAudioAt = 0;
  }

  function setCaptureStreamProvider(fn) {
    captureStreamProvider = typeof fn === "function" ? fn : null;
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
    resetSilenceSuppression,
    updatePcmSenderState,
    scheduleAudioKeepalive,
    clearAudioKeepaliveTimer,
    sendAudioKeepaliveNow,
    setAudioKeepaliveMs,
    setAudioKeepaliveIdleMs,
  };
}
