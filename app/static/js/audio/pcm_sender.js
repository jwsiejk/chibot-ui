/* eslint-disable no-console */

const DEFAULT_CHUNK_MS = 60;
const DEFAULT_FLUSH_MS = 50;
const TARGET_SAMPLE_RATE = 16000;

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

function ensureAudioContext() {
  if (typeof window === "undefined") {
    throw new Error("AudioContext unavailable: window not defined");
  }
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) {
    throw new Error("Web Audio API is not supported in this browser");
  }
  return new AudioContextCtor();
}

export async function initPcmSender(ws, {
  onSampleRate,
  onFrame,
  onSend,
  onError,
  chunkMs = DEFAULT_CHUNK_MS,
  flushIntervalMs = DEFAULT_FLUSH_MS,
} = {}) {
  if (!ws || typeof ws.send !== "function") {
    throw new Error("initPcmSender requires an active WebSocket");
  }

  const audioCtx = ensureAudioContext();
  const mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const source = audioCtx.createMediaStreamSource(mediaStream);
  const bufferSize = 2048;
  const processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
  let activeWs = ws;
  let enabled = false;
  let seq = 0;
  let queue = [];
  let queuedSamples = 0;
  let flushTimer = null;

  const sampleRate = audioCtx.sampleRate;
  console.log("[pcm_sender] audioCtx.sampleRate", sampleRate);
  const targetSampleRate = TARGET_SAMPLE_RATE;
  const samplesPerMs = targetSampleRate / 1000;

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

  function sendConfigFrame(targetWs) {
    if (!targetWs || typeof targetWs.send !== "function") {
      return;
    }
    const payload = JSON.stringify({ type: "config", sampleRate });
    const attemptSend = () => {
      try {
        targetWs.send(payload);
      } catch (err) {
        if (typeof onError === "function") {
          try { onError(err); } catch (_) {}
        }
        console.warn("[pcm_sender] failed to send config frame", err);
      }
    };
    if (targetWs.readyState === WebSocket.OPEN) {
      attemptSend();
    } else {
      const handleOpen = () => {
        targetWs.removeEventListener("open", handleOpen);
        attemptSend();
      };
      targetWs.addEventListener("open", handleOpen);
    }
  }

  invokeSampleRate(sampleRate);
  sendConfigFrame(activeWs);

  function scheduleFlush() {
    if (!flushTimer) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        flushQueue();
      }, flushIntervalMs);
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
    if (!activeWs || activeWs.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      activeWs.send(new Uint8Array(out.buffer));
      if (typeof onSend === "function") {
        try {
          onSend(out, {
            seq: firstSeq == null ? seq : firstSeq,
            samples: out.length,
            bytes: out.byteLength,
            chunkCount,
            sampleRate: targetSampleRate,
          });
        } catch (err) {
          console.warn("[pcm_sender] onSend callback failed", err);
        }
      }
      seq += 1;
    } catch (err) {
      notifyError(err);
      console.warn("[pcm_sender] failed to send audio chunk", err);
    }
  }

  processor.onaudioprocess = (event) => {
    if (!event || !event.inputBuffer || event.inputBuffer.numberOfChannels === 0) {
      return;
    }
    const channelData = event.inputBuffer.getChannelData(0);
    if (!channelData || !channelData.length) {
      return;
    }
    const downsampled = downsampleTo16k(channelData, sampleRate);
    if (!downsampled.length) {
      return;
    }
    const pcm16 = floatTo16PCM(downsampled);
    pcm16.__seq = seq;

    if (typeof onFrame === "function") {
      try {
        onFrame(pcm16, {
          seq,
          bytes: pcm16.byteLength,
          samples: pcm16.length,
          sampleRate: targetSampleRate,
          timestamp: (typeof performance !== "undefined" && typeof performance.now === "function")
            ? performance.now()
            : Date.now(),
        });
      } catch (err) {
        console.warn("[pcm_sender] onFrame callback failed", err);
      }
    }

    if (!enabled) {
      return;
    }

    queue.push(pcm16);
    queuedSamples += pcm16.length;
    if ((queuedSamples / samplesPerMs) >= chunkMs) {
      flushQueue();
    } else {
      scheduleFlush();
    }
  };

  source.connect(processor);
  processor.connect(audioCtx.destination);

  async function resume() {
    if (audioCtx.state === "suspended") {
      try {
        await audioCtx.resume();
      } catch (err) {
        notifyError(err);
        console.warn("[pcm_sender] failed to resume AudioContext", err);
      }
    }
  }

  function setEnabled(nextEnabled) {
    const desired = Boolean(nextEnabled);
    if (enabled === desired) {
      return;
    }
    enabled = desired;
    if (!enabled) {
      resetQueue();
    }
  }

  function setWebSocket(nextWs) {
    if (nextWs && typeof nextWs.send !== "function") {
      console.warn("[pcm_sender] setWebSocket ignored: invalid target");
      return;
    }
    activeWs = nextWs || null;
    if (activeWs) {
      sendConfigFrame(activeWs);
    }
  }

  async function destroy() {
    try {
      setEnabled(false);
      processor.disconnect();
    } catch (err) {
      console.warn("[pcm_sender] processor disconnect failed", err);
    }
    try {
      source.disconnect();
    } catch (err) {
      console.warn("[pcm_sender] source disconnect failed", err);
    }
    try {
      mediaStream.getTracks().forEach((track) => {
        try { track.stop(); } catch (_) {}
      });
    } catch (err) {
      console.warn("[pcm_sender] failed to stop media tracks", err);
    }
    try {
      await audioCtx.close();
    } catch (err) {
      console.warn("[pcm_sender] audio context close failed", err);
    }
  }

  return {
    audioContext: audioCtx,
    mediaStream,
    resume,
    setEnabled,
    setWebSocket,
    destroy,
  };
}

export { floatTo16PCM, downsampleTo16k };
