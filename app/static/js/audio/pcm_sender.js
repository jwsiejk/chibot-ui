import * as telemetry from "../ws/telemetry.js";
import * as versionModule from "../version.js";

const emitClientLog = typeof telemetry.emitClientLog === "function"
  ? telemetry.emitClientLog
  : () => {};
const logStage = typeof telemetry.logStage === "function"
  ? telemetry.logStage
  : () => {};

const DEFAULT_CHUNK_MS = 60;
const DEFAULT_FLUSH_MS = 50;
const TARGET_SAMPLE_RATE = 16000;

function withCacheBuster(path) {
  if (!path || typeof path !== "string") {
    return path;
  }
  const withVersionFn = typeof versionModule.withVersion === "function"
    ? versionModule.withVersion
    : (value) => value;
  return withVersionFn(path);
}

const WORKLET_PATH = withCacheBuster("/static/js/audio/pcm-worklet-processor.js");

function floatTo16PCM(float32Samples) {
  if (!float32Samples || typeof float32Samples.length !== "number") {
    return new Int16Array(0);
  }
  const buffer = new ArrayBuffer(float32Samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < float32Samples.length; i += 1) {
    let sample = float32Samples[i];
    if (!Number.isFinite(sample)) {
      sample = 0;
    }
    const clamped = Math.max(-1, Math.min(1, sample));
    const intSample = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(i * 2, intSample, true);
  }
  return new Int16Array(buffer);
}

function downsampleTo16k(float32Samples, inputSampleRate) {
  if (!float32Samples || typeof float32Samples.length !== "number") {
    return new Float32Array(0);
  }
  if (!float32Samples.length) {
    return new Float32Array(0);
  }
  const source = float32Samples;
  if (!Number.isFinite(inputSampleRate) || inputSampleRate <= 0) {
    return source;
  }
  if (inputSampleRate === TARGET_SAMPLE_RATE) {
    return source;
  }
  const ratio = inputSampleRate / TARGET_SAMPLE_RATE;
  if (!Number.isFinite(ratio) || ratio <= 0) {
    return source;
  }
  const expectedLength = source.length / ratio;
  const outLength = Math.max(1, Math.round(expectedLength));
  const result = new Float32Array(outLength);
  for (let i = 0; i < outLength; i += 1) {
    const srcPosition = i * ratio;
    const srcIndex = Math.floor(srcPosition);
    const nextIndex = Math.min(source.length - 1, srcIndex + 1);
    const interp = srcPosition - srcIndex;
    const sample = source[srcIndex] + (source[nextIndex] - source[srcIndex]) * interp;
    result[i] = sample;
  }
  return result;
}

export async function initPcmSender(mediaStream = null, {
  audioCtx,
  onSampleRate,
  onFrame,
  onSend,
  onError,
  chunkMs = DEFAULT_CHUNK_MS,
  flushIntervalMs = DEFAULT_FLUSH_MS,
} = {}) {
  if (!audioCtx) {
    throw new Error("AudioContext is required for pcm_sender");
  }
  const chunkDurationMs = Number.isFinite(chunkMs) && chunkMs > 0 ? chunkMs : DEFAULT_CHUNK_MS;
  const flushMs = Number.isFinite(flushIntervalMs) && flushIntervalMs > 0
    ? flushIntervalMs
    : DEFAULT_FLUSH_MS;
  const targetSampleRate = TARGET_SAMPLE_RATE;
  const frameHintMs = Math.max(10, Math.min(chunkDurationMs, flushMs));
  const minFrameSamples = Math.max(160, Math.round((frameHintMs / 1000) * targetSampleRate));
  const maxFrameSamples = Math.max(minFrameSamples, Math.round((chunkDurationMs / 1000) * targetSampleRate));

  try {
    console.log("AskChip pcm_sender initialized", {
      sampleRate: targetSampleRate,
      encoding: "pcm16", // implicit from AudioWorklet
      chunkMs: chunkDurationMs,
      flushIntervalMs: flushMs,
    });
  } catch (_) {}

  try {
    emitClientLog("client.pcm_sender.init", {
      targetSampleRate,
      chunkMs: chunkDurationMs,
      flushIntervalMs: flushMs,
      channels: 1,
    });
  } catch (_) {}

  const supportsWorklet = Boolean(
    audioCtx.audioWorklet && typeof audioCtx.audioWorklet.addModule === "function",
  );
  let workletPort = null;
  let usingWorklet = false;
  let processor = null;
  let sinkNode = null;
  let workletCallbackSeen = false;
  let processorCallbackSeen = false;
  let firstPcmLogEmitted = false;

  if (supportsWorklet) {
    try {
      await audioCtx.audioWorklet.addModule(WORKLET_PATH);
      processor = new AudioWorkletNode(audioCtx, "pcm-worklet-processor", {
        numberOfOutputs: 0,
        processorOptions: {
          targetSampleRate,
          minFrameSamples,
          maxFrameSamples,
        },
      });
      workletPort = processor.port;
      usingWorklet = true;
    } catch (err) {
      console.warn("[pcm_sender] failed to initialize AudioWorkletNode; falling back", err);
    }
  }

  if (!mediaStream) {
    throw new Error("MediaStream is required to initialize pcm_sender");
  }
  const audioTracks = mediaStream?.getAudioTracks?.();
  const primaryTrack = audioTracks && audioTracks.length ? audioTracks[0] : null;
  const deviceId = primaryTrack?.getSettings?.()?.deviceId || null;
    console.log('client.mic_stream_acquired_ok', {
      deviceId,
      sampleRate: audioCtx?.sampleRate || null,
    });
  try {
    emitClientLog("client.pcm_sender.acquired_stream", {
      audioCtxState: audioCtx?.state || null,
      audioCtxSampleRate: audioCtx?.sampleRate || null,
      mediaStreamId: mediaStream?.id || null,
    });
  } catch (_) {}

  const source = audioCtx.createMediaStreamSource(mediaStream);

  if (!processor) {
    const bufferSize = 2048;
    processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
  }
  let enabled = false;
  let seq = 0;
  let queue = [];
  let queuedSamples = 0;
  let flushTimer = null;

  const sampleRate = audioCtx.sampleRate;
  console.log("[pcm_sender] audioCtx.sampleRate", sampleRate);
  let effectiveSampleRate = targetSampleRate;
  let samplesPerMs = effectiveSampleRate / 1000;

  function clearFlushTimer() {
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
  }

  function resetQueue() {
    queue = [];
    queuedSamples = 0;
    clearFlushTimer();
  }

  function invokeSampleRate(sampleRateValue) {
    if (typeof onSampleRate === "function") {
      try {
        onSampleRate(sampleRateValue, {
          targetSampleRate,
        });
      } catch (err) {
        console.warn("[pcm_sender] onSampleRate callback failed", err);
      }
    }
  }

  // Notify listeners of the detected input rate. Protocol metadata is sent via
  // the dedicated audio.header path in ws_client.js; avoid emitting legacy
  // config frames that the server no longer understands.
  invokeSampleRate(sampleRate);

  function scheduleFlush() {
    if (!flushTimer) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        flushQueue();
      }, flushMs);
    }
  }

  function notifyError(err) {
    if (typeof onError === "function") {
      try {
        onError(err);
      } catch (callbackErr) {
        console.warn("[pcm_sender] onError callback failed", callbackErr);
      }
    }
  }

  function handleFrame(pcm16, frameInfo = {}) {
    if (!pcm16 || typeof pcm16.length !== "number" || pcm16.length === 0) {
      return;
    }

    const frameSampleRateCandidate = frameInfo && typeof frameInfo.sampleRate === "number"
      ? frameInfo.sampleRate
      : null;
    if (Number.isFinite(frameSampleRateCandidate) && frameSampleRateCandidate > 0) {
      effectiveSampleRate = frameSampleRateCandidate;
      samplesPerMs = effectiveSampleRate / 1000;
      if (!Number.isFinite(samplesPerMs) || samplesPerMs <= 0) {
        samplesPerMs = targetSampleRate / 1000;
      }
    }

    const timestamp = frameInfo && typeof frameInfo.timestamp === "number"
      ? frameInfo.timestamp
      : ((typeof performance !== "undefined" && typeof performance.now === "function")
        ? performance.now()
        : Date.now());

    pcm16.__seq = seq;

    if (typeof onFrame === "function") {
      try {
        onFrame(pcm16, {
          seq,
          bytes: pcm16.byteLength,
          samples: pcm16.length,
          sampleRate: effectiveSampleRate,
          timestamp,
        });
      } catch (err) {
        console.warn("[pcm_sender] onFrame callback failed", err);
      }
    }

    if (!firstPcmLogEmitted && pcm16.length > 0) {
      firstPcmLogEmitted = true;
      try {
        logStage("mic_debug.first_pcm_client", {
          ts: typeof performance?.now === "function" ? performance.now() : Date.now(),
          sampleRate,
          targetSampleRate,
        });
      } catch (_) {}
    }

    if (!enabled) {
      return;
    }

    queue.push(pcm16);
    queuedSamples += pcm16.length;

    const queuedMs = samplesPerMs > 0 ? (queuedSamples / samplesPerMs) : chunkDurationMs;
    if (queuedMs >= chunkDurationMs) {
      flushQueue();
    } else {
      scheduleFlush();
    }
  }

  function flushQueue() {
    if (!enabled || !queuedSamples) {
      resetQueue();
      return;
    }
    const total = queuedSamples;
    const out = new Int16Array(total);
    let offset = 0;
    let chunkCount = 0;
    let firstSeq = null;
    for (const frame of queue) {
      if (!frame || !frame.length) {
        continue;
      }
      if (firstSeq === null && typeof frame.__seq === "number") {
        firstSeq = frame.__seq;
      }
      out.set(frame, offset);
      offset += frame.length;
      chunkCount += 1;
    }
    resetQueue();
    if (!offset) {
      return;
    }
    const chunkSeq = firstSeq == null ? seq : firstSeq;
    if (emitChunk(out, { chunkCount, seq: chunkSeq })) {
      seq += 1;
    }
  }

  function sendImmediate(pcm16, meta = {}) {
    if (!pcm16 || typeof pcm16.length !== "number" || pcm16.length === 0) {
      return false;
    }
    let typed = pcm16 instanceof Int16Array ? pcm16 : null;
    if (!typed) {
      try {
        typed = new Int16Array(pcm16);
      } catch (err) {
        console.warn("[pcm_sender] sendImmediate failed to coerce chunk", err);
        return false;
      }
    }
    if (!(typed instanceof Int16Array)) {
      return false;
    }
    if (typed.byteOffset !== 0 || typed.byteLength !== typed.buffer.byteLength) {
      typed = typed.slice();
    }
    resetQueue();
    if (emitChunk(typed, {
      chunkCount: Number.isFinite(meta?.chunkCount) && meta.chunkCount > 0
        ? Number(meta.chunkCount)
        : 1,
      sampleRate: Number.isFinite(meta?.sampleRate) && meta.sampleRate > 0
        ? Number(meta.sampleRate)
        : effectiveSampleRate,
      seq,
    })) {
      seq += 1;
      return true;
    }
    return false;
  }

  function emitChunk(pcm16, meta = {}) {
    if (!pcm16 || typeof pcm16.length !== "number" || pcm16.length === 0) {
      return false;
    }
    if (!enabled) {
      return false;
    }
    let chunk = pcm16;
    if (!(chunk instanceof Int16Array)) {
      try {
        chunk = new Int16Array(pcm16);
      } catch (err) {
        console.warn("[pcm_sender] emitChunk failed to coerce", err);
        return false;
      }
    }
    if (chunk.byteOffset !== 0 || chunk.byteLength !== chunk.buffer.byteLength) {
      chunk = chunk.slice();
    }
    const sampleRateMeta = Number.isFinite(meta.sampleRate) && meta.sampleRate > 0
      ? Number(meta.sampleRate)
      : effectiveSampleRate;
    const chunkCountMeta = Number.isFinite(meta.chunkCount) && meta.chunkCount > 0
      ? Number(meta.chunkCount)
      : 1;
    const seqMeta = Number.isFinite(meta.seq) ? Number(meta.seq) : seq;
    if (typeof onSend === "function") {
      try {
        onSend(chunk, {
          seq: seqMeta,
          samples: chunk.length,
          bytes: chunk.byteLength,
          chunkCount: chunkCountMeta,
          sampleRate: sampleRateMeta,
        });
      } catch (err) {
        notifyError(err);
        console.warn("[pcm_sender] onSend callback failed", err);
        return false;
      }
    }
    return true;
  }

  if (usingWorklet && workletPort) {
    workletPort.onmessage = (event) => {
      const message = event && event.data ? event.data : null;
      if (!message || message.type !== "pcm16" || !message.buffer) {
        return;
      }
      const frameSampleRate = Number.isFinite(message.sampleRate) && message.sampleRate > 0
        ? message.sampleRate
        : targetSampleRate;
      if (!workletCallbackSeen) {
        workletCallbackSeen = true;
        try {
          emitClientLog("client.pcm_sender.worklet_callback", {
            sampleRate: frameSampleRate,
          });
        } catch (_) {}
      }
      const pcm16 = new Int16Array(message.buffer);
      handleFrame(pcm16, { sampleRate: frameSampleRate, timestamp: message.timestamp });
    };
  } else if (processor) {
    processor.onaudioprocess = (event) => {
      if (!event || !event.inputBuffer || event.inputBuffer.numberOfChannels === 0) {
        return;
      }
      const channelData = event.inputBuffer.getChannelData(0);
      if (!channelData || !channelData.length) {
        return;
      }
      if (!processorCallbackSeen) {
        processorCallbackSeen = true;
        try {
          emitClientLog("client.pcm_sender.processor_callback", {
            sampleRate,
          });
        } catch (_) {}
      }
      const downsampled = downsampleTo16k(channelData, sampleRate);
      if (!downsampled.length) {
        return;
      }
      const timestamp = typeof event.timeStamp === "number" ? event.timeStamp : undefined;
      const pcm16 = floatTo16PCM(downsampled);
      handleFrame(pcm16, { sampleRate: targetSampleRate, timestamp });
    };
  }

  try {
    source.connect(processor);
    if (!usingWorklet && processor && typeof processor.connect === "function") {
      sinkNode = audioCtx.createGain();
      sinkNode.gain.value = 0;

      processor.connect(sinkNode);
      // Do not connect any mic-origin nodes to an audible sink.
      sinkNode.connect(audioCtx.createMediaStreamDestination());
    }
    try {
      logStage("client.pcm_sender.node_connect_success", {
        usingWorklet,
        audioCtxState: audioCtx?.state || null,
        sampleRate,
      });
    } catch (_) {}
  } catch (err) {
    notifyError(err);
    console.warn("[pcm_sender] failed to connect audio nodes", err);
    try {
      logStage("client.pcm_sender.node_connect_failure", {
        audioCtxState: audioCtx?.state || null,
        error_name: err?.name || null,
        error_message: err?.message || null,
      });
    } catch (_) {}
    if (usingWorklet && workletPort) {
      try { workletPort.onmessage = null; } catch (_) {}
    } else if (processor) {
      try { processor.onaudioprocess = null; } catch (_) {}
    }
    try { processor.disconnect(); } catch (_) {}
    try { sinkNode?.disconnect?.(); } catch (_) {}
    try { source.disconnect(); } catch (_) {}
    throw err;
  }

  async function resume() {
    // Context lifecycle is managed by callers; pcm_sender must not resume or
    // route mic audio toward an audible destination.
  }

  function setEnabled(nextEnabled) {
    const desired = Boolean(nextEnabled);
    if (enabled === desired) {
      return;
    }
    try {
      if (desired) {
        emitClientLog("client.pcm_sender.enabled", {});
      } else {
        emitClientLog("client.pcm_sender.disabled", {});
      }
    } catch (_) {}
    enabled = desired;
    if (enabled) {
      resume().catch((err) => {
        console.warn("[pcm_sender] resume from setEnabled failed", err);
      });
    }
    if (!enabled) {
      resetQueue();
    }
  }

  async function destroy() {
    try {
      setEnabled(false);
      if (usingWorklet && workletPort) {
        try {
          workletPort.postMessage({ type: "flush" });
        } catch (err) {
          console.warn("[pcm_sender] worklet flush failed", err);
        }
        try {
          workletPort.onmessage = null;
        } catch (_) {}
      } else if (processor) {
        try {
          processor.onaudioprocess = null;
        } catch (_) {}
      }
      if (processor && typeof processor.disconnect === "function") {
        try {
          processor.disconnect();
        } catch (err) {
          console.warn("[pcm_sender] processor disconnect failed", err);
        }
      }
      if (sinkNode && typeof sinkNode.disconnect === "function") {
        try {
          sinkNode.disconnect();
        } catch (err) {
          console.warn("[pcm_sender] sinkNode disconnect failed", err);
        }
      }
    } catch (err) {
      console.warn("[pcm_sender] processor teardown failed", err);
    }
    try {
      source.disconnect();
    } catch (err) {
      console.warn("[pcm_sender] source disconnect failed", err);
    }
  }

  function getStateSnapshot() {
    const tracks = mediaStream?.getAudioTracks?.() || [];
    const trackStates = [];
    for (const track of tracks) {
      trackStates.push({
        id: track?.id || null,
        kind: track?.kind || null,
        label: track?.label || null,
        enabled: Boolean(track?.enabled),
        muted: Boolean(track?.muted),
        readyState: track?.readyState || null,
      });
    }
    return {
      enabled,
      mediaStreamActive: Boolean(mediaStream?.active),
      mediaStreamId: mediaStream?.id || null,
      audioContextState: audioCtx?.state || null,
      trackCount: trackStates.length,
      tracks: trackStates,
    };
  }

  return {
    audioContext: audioCtx,
    mediaStream,
    getStateSnapshot,
    resume,
    setEnabled,
    sendImmediate,
    destroy,
  };
}

export { floatTo16PCM, downsampleTo16k };
