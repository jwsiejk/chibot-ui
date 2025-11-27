(() => {
  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading AudioPlayer");
  }

  const emitClientLog = typeof window !== "undefined" ? window.emitClientLog : null;
  const AUDIOPLAYER_DEBUG_HARD_MUTE = true; // set true to mute all playback

  const AUDIO_DEBUG_MAX_LOGS = 10;
  let audioDebugLogCount = 0;

  const INT16_DIVISOR = 32768;
  const ACTIVE_SOURCES = new Set();

  let descriptor = null;
  let audioContext = null;
  let gainNode = null;
  let nextStartTime = 0;
  let decodeChain = Promise.resolve();
  let flushToken = 0;
  let currentUtteranceId = null;
  let pendingDrainUttId = null;
  let audioStartReported = false;

  function normalizeDescriptor(meta) {
    if (!meta || typeof meta !== "object") return null;
    const rateCandidate = Number(meta.rate_hz ?? meta.sampleRate ?? meta.sample_rate ?? meta.rate);
    const channelsCandidate = Number(meta.channels ?? meta.channel_count ?? meta.num_channels);
    if (!Number.isFinite(rateCandidate) || rateCandidate <= 0) return null;
    const channels = Number.isFinite(channelsCandidate) && channelsCandidate > 0 ? channelsCandidate : 1;
    return { sampleRate: rateCandidate, channels };
  }

  function closeContext() {
    if (!audioContext) return;
    try {
      audioContext.close();
    } catch (err) {
      console.warn("AudioPlayer failed to close AudioContext", err);
    }
    audioContext = null;
    gainNode = null;
    nextStartTime = 0;
  }

  function ensureContext() {
    if (!descriptor) return null;
    const desiredRate = descriptor.sampleRate;
    if (audioContext && Math.abs(audioContext.sampleRate - desiredRate) > 1) {
      closeContext();
    }
    if (!audioContext) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) {
        console.error("Web Audio API is not supported in this browser");
        return null;
      }
      try {
        audioContext = new Ctor({ sampleRate: desiredRate });
      } catch (err) {
        console.error("AudioPlayer failed to create AudioContext", err);
        audioContext = null;
        return null;
      }
      gainNode = audioContext.createGain();
      gainNode.connect(audioContext.destination);
      nextStartTime = audioContext.currentTime;
    }
    return audioContext;
  }

  function extractUttId(payload) {
    if (!payload || typeof payload !== "object") return null;
    if (typeof payload.utt_id === "string" && payload.utt_id) {
      return payload.utt_id;
    }
    const meta = payload.meta;
    if (meta && typeof meta === "object") {
      const ttsMeta = meta.tts;
      if (ttsMeta && typeof ttsMeta === "object" && typeof ttsMeta.utt_id === "string" && ttsMeta.utt_id) {
        return ttsMeta.utt_id;
      }
    }
    return null;
  }

  function extractReason(payload) {
    if (!payload || typeof payload !== "object") return null;
    if (typeof payload.reason === "string" && payload.reason) {
      return payload.reason;
    }
    const meta = payload.meta;
    if (meta && typeof meta === "object") {
      if (typeof meta.reason === "string" && meta.reason) {
        return meta.reason;
      }
      const ttsMeta = meta.tts;
      if (ttsMeta && typeof ttsMeta === "object" && typeof ttsMeta.reason === "string" && ttsMeta.reason) {
        return ttsMeta.reason;
      }
    }
    return null;
  }

  function finalizeUtterance() {
    const completedUtt = pendingDrainUttId || currentUtteranceId || null;
    pendingDrainUttId = null;
    currentUtteranceId = null;
    audioStartReported = false;
    AppState.setState({ tAudioStartMs: null, ttsUttId: null });
    if (typeof window !== "undefined") {
      try {
        window.dispatchEvent(
          new CustomEvent("local_audio.ended", {
            detail: completedUtt ? { uttId: completedUtt } : { uttId: null }
          })
        );
      } catch (err) {
        console.warn("AudioPlayer local_audio.ended dispatch failed", err);
      }
    }
  }

  function interrupt({ preserveState = false } = {}) {
    flushToken += 1;
    decodeChain = Promise.resolve();
    const ctx = audioContext;
    ACTIVE_SOURCES.forEach((source) => {
      try {
        source.stop();
      } catch (err) {
        // no-op
      }
      try {
        source.disconnect();
      } catch (err) {
        // no-op
      }
    });
    ACTIVE_SOURCES.clear();
    if (ctx) {
      nextStartTime = ctx.currentTime;
    } else {
      nextStartTime = 0;
    }
    pendingDrainUttId = null;
    audioStartReported = false;
    if (!preserveState) {
      finalizeUtterance();
    }
  }

  function scheduleBuffer(float32, token) {
    if (!descriptor || flushToken !== token) return;
    const ctx = ensureContext();
    if (!ctx || flushToken !== token) return;
    if (float32.length === 0) return;
    const channels = descriptor.channels || 1;
    const buffer = ctx.createBuffer(channels, float32.length, ctx.sampleRate);
    for (let ch = 0; ch < channels; ch += 1) {
      buffer.copyToChannel(float32, ch);
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    if (AUDIOPLAYER_DEBUG_HARD_MUTE) {
      try {
        console.log("AudioPlayer hard mute active; skipping buffer playback");
      } catch (_) {}
      if (ctx) {
        const silentDuration = buffer.duration || 0;
        nextStartTime = Math.max(ctx.currentTime, nextStartTime) + silentDuration;
      }
      return;
    }
    source.connect(gainNode);
    const safeLeadTime = 0.005;
    const startAt = Math.max(nextStartTime, ctx.currentTime + safeLeadTime);
    try {
      source.start(startAt);
    } catch (err) {
      console.error("AudioPlayer failed to start buffer source", err);
      return;
    }
    nextStartTime = startAt + buffer.duration;
    ACTIVE_SOURCES.add(source);
    source.onended = () => {
      ACTIVE_SOURCES.delete(source);
      if (ACTIVE_SOURCES.size === 0) {
        if (audioContext) {
          nextStartTime = audioContext.currentTime;
        }
        if (!pendingDrainUttId || (currentUtteranceId && pendingDrainUttId !== currentUtteranceId)) {
          return;
        }
        finalizeUtterance();
      }
    };
    if (!audioStartReported && currentUtteranceId) {
      audioStartReported = true;
      const ctxNow = ctx.currentTime;
      const delayMs = Math.max(0, startAt - ctxNow) * 1000;
      const startMs = Date.now() + delayMs;
      AppState.setState({ tAudioStartMs: startMs, ttsUttId: currentUtteranceId });
    }
  }

  function enqueueChunk(chunk) {
    try {
      if (!descriptor) {
        console.warn("AudioPlayer received chunk without descriptor; dropping");
        return;
      }
      if (!chunk) {
        return;
      }

      let normalizedChunk = chunk;
      if (ArrayBuffer.isView?.(normalizedChunk)) {
        normalizedChunk = normalizedChunk.buffer.slice(
          normalizedChunk.byteOffset,
          normalizedChunk.byteOffset + normalizedChunk.byteLength,
        );
      }

      const byteLength = normalizedChunk instanceof Blob
        ? normalizedChunk.size || 0
        : normalizedChunk?.byteLength || 0;

      if (audioDebugLogCount < AUDIO_DEBUG_MAX_LOGS) {
        audioDebugLogCount += 1;
        try {
          console.log("client.audio_player.enqueue_chunk", { size: byteLength, count: audioDebugLogCount });
        } catch (_) {}
        try {
          if (audioDebugLogCount <= 3 && typeof emitClientLog === "function") {
            emitClientLog("client.audio_player.enqueue_chunk", { size: byteLength });
          }
        } catch (_) {}
      }

      const token = flushToken;
      decodeChain = decodeChain
        .then(async () => {
          const ctx = ensureContext();
          if (!ctx || flushToken !== token) return;
          let arrayBuffer;
          if (normalizedChunk instanceof ArrayBuffer) {
            arrayBuffer = normalizedChunk;
          } else if (normalizedChunk instanceof Blob) {
            arrayBuffer = await normalizedChunk.arrayBuffer();
          } else {
            console.warn("AudioPlayer received unsupported chunk", normalizedChunk);
            return;
          }
          if (!arrayBuffer || flushToken !== token) return;
          const int16 = new Int16Array(arrayBuffer);
          if (!int16.length) return;
          const float32 = new Float32Array(int16.length);
          for (let i = 0; i < int16.length; i += 1) {
            float32[i] = Math.max(-1, Math.min(1, int16[i] / INT16_DIVISOR));
          }
          if (flushToken !== token) return;
          try {
            await ctx.resume();
          } catch (err) {
            // Some browsers throw if resume is called redundantly; ignore.
          }
          scheduleBuffer(float32, token);
        })
        .catch((err) => {
          console.error("AudioPlayer chunk enqueue failed", err);
        });
    } catch (err) {
      try {
        console.warn("AudioPlayer.enqueueChunk failed", err);
      } catch (_) {}
    }
  }

  function setDescriptor(meta) {
    const normalized = normalizeDescriptor(meta);
    if (!normalized) {
      console.warn("AudioPlayer received invalid descriptor", meta);
      return;
    }
    descriptor = normalized;
    ensureContext();

    try {
      console.log("client.audio_player.descriptor_set", {
        sampleRate: normalized.sampleRate,
        channels: normalized.channels,
      });
    } catch (_) {}
    try {
      if (typeof emitClientLog === "function") {
        emitClientLog("client.audio_player.descriptor_set", {
          sampleRate: normalized.sampleRate,
          channels: normalized.channels,
        });
      }
    } catch (_) {}
  }

  function handleTtsStart(frame) {
    interrupt({ preserveState: true });
    currentUtteranceId = extractUttId(frame);
    pendingDrainUttId = null;
    audioStartReported = false;
    if (currentUtteranceId) {
      AppState.setState({ ttsUttId: currentUtteranceId, tAudioStartMs: null });
    } else {
      AppState.setState({ tAudioStartMs: null });
    }

    try {
      console.log("client.audio_player.tts_start", {
        uttId: currentUtteranceId || null,
      });
    } catch (_) {}
  }

  function handleTtsEnd(frame) {
    const reasonRaw = extractReason(frame);
    const reason = typeof reasonRaw === "string" ? reasonRaw.toLowerCase() : null;
    if (reason && !["completed", "finished", "complete", "done"].includes(reason)) {
      interrupt();
      return;
    }
    const uttId = extractUttId(frame);
    pendingDrainUttId = uttId || currentUtteranceId;
    if (ACTIVE_SOURCES.size === 0) {
      finalizeUtterance();
    }

    try {
      console.log("client.audio_player.tts_end", {
        uttId: currentUtteranceId || null,
      });
    } catch (_) {}
  }

  window.AudioPlayer = {
    setDescriptor,
    enqueueChunk,
    handleTtsStart,
    handleTtsEnd,
    interrupt,
  };

  window.addEventListener("ws.close", () => {
    interrupt();
  });
})();
