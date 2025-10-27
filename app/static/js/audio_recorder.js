(() => {
  const AppState = window.AppState;
  if (!AppState) {
    throw new Error("AppState store is required before loading AudioRecorder");
  }

  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  const BACKPRESSURE_LIMIT_BYTES = 512 * 1024;
  const WEBM_TIMESLICE_MS = 250;
  const POST_TTS_RELEASE_DELAY_MS = 150;

  class PcmDownsampler {
    constructor(sourceRate, targetRate) {
      this.sourceRate = sourceRate;
      this.targetRate = targetRate;
      this._ratio = sourceRate / targetRate;
      this._buffer = [];
      this._position = 0;
    }

    reset() {
      this._buffer.length = 0;
      this._position = 0;
    }

    push(samples) {
      if (!samples || samples.length === 0) return;
      for (let i = 0; i < samples.length; i += 1) {
        this._buffer.push(samples[i]);
      }
    }

    pullInt16() {
      const buffer = this._buffer;
      if (!buffer.length) return null;
      const ratio = this._ratio;
      const output = [];
      let pos = this._position;
      const total = buffer.length;
      while (pos + 1 < total) {
        const base = Math.floor(pos);
        const frac = pos - base;
        const next = base + 1;
        if (next >= total) break;
        const sample = buffer[base] + (buffer[next] - buffer[base]) * frac;
        output.push(sample);
        pos += ratio;
      }
      let consumed = Math.floor(pos);
      const maxConsumable = Math.max(0, buffer.length - 2);
      if (consumed > maxConsumable) consumed = maxConsumable;
      if (consumed > 0) {
        buffer.splice(0, consumed);
        pos -= consumed;
      }
      this._position = pos;
      if (!output.length) return null;
      const out = new Int16Array(output.length);
      for (let i = 0; i < output.length; i += 1) {
        const value = Math.max(-1, Math.min(1, output[i]));
        out[i] = value < 0 ? Math.round(value * 32768) : Math.round(value * 32767);
      }
      return out;
    }
  }

  function downmixToMono(channels) {
    if (!channels || !channels.length) {
      return new Float32Array(0);
    }
    if (channels.length === 1) {
      return new Float32Array(channels[0]);
    }
    const length = channels[0].length;
    const mono = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      let sum = 0;
      for (let c = 0; c < channels.length; c += 1) {
        sum += channels[c][i] || 0;
      }
      mono[i] = sum / channels.length;
    }
    return mono;
  }

  function computePayloadSize(payload) {
    if (payload instanceof Blob) return payload.size;
    if (payload && typeof payload.byteLength === "number") return payload.byteLength;
    if (payload && typeof payload.length === "number") return payload.length;
    return 0;
  }

  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";

  function emitCustomEvent(type, detail) {
    try {
      window.dispatchEvent(new CustomEvent(type, { detail }));
    } catch (err) {
      console.warn("AudioRecorder event dispatch failed", type, err);
    }
  }

  const recorder = {
    _stream: null,
    _startPromise: null,
    _mediaRecorder: null,
    _audioCtx: null,
    _sourceNode: null,
    _processorNode: null,
    _muteNode: null,
    _downsampler: null,
    _descriptor: null,
    _pendingDescriptor: null,
    _vendor: null,
    _asrReady: false,
    _mask: { active: false, holdMs: 0, timerId: null },
    _listening: false,
    _micOpenEmitted: false,
    _bargeInEnabled: true,

    async start() {
      if (this._stream) {
        this._setListening(true);
        this._maybeActivateCapture();
        return this._stream;
      }
      if (this._startPromise) return this._startPromise;
      const request = navigator.mediaDevices
        .getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: false
          }
        })
        .then((stream) => {
          this._stream = stream;
          if (this._pendingDescriptor) {
            this._activateDescriptor(this._pendingDescriptor);
            this._pendingDescriptor = null;
          }
          this._setListening(true);
          this._maybeActivateCapture();
          return stream;
        })
        .catch((err) => {
          console.error("AudioRecorder start failed", err);
          throw err;
        })
        .finally(() => {
          this._startPromise = null;
        });
      this._startPromise = request;
      return request;
    },

    stop() {
      this._teardownEncoder();
      if (this._stream) {
        const tracks = this._stream.getTracks();
        tracks.forEach((track) => {
          try {
            track.stop();
          } catch (err) {
            console.warn("Failed to stop track", err);
          }
        });
        this._stream = null;
      }
      this._descriptor = null;
      this._pendingDescriptor = null;
      this._vendor = null;
      this._asrReady = false;
      this._setMask(false);
      this._setListening(false, { resetMicEvent: true });
    },

    _teardownEncoder() {
      this._asrReady = false;
      if (this._mediaRecorder) {
        try {
          if (this._mediaRecorder.state !== "inactive") {
            this._mediaRecorder.stop();
          }
        } catch (err) {
          console.warn("MediaRecorder stop error", err);
        }
        this._mediaRecorder.removeEventListener("dataavailable", this._handleWebmData);
        this._mediaRecorder.removeEventListener("error", this._handleRecorderError);
        this._mediaRecorder = null;
        this._handleWebmData = null;
        this._handleRecorderError = null;
      }
      if (this._processorNode) {
        try {
          this._processorNode.disconnect();
        } catch (err) {
          console.warn("Processor disconnect error", err);
        }
        this._processorNode.onaudioprocess = null;
        this._processorNode = null;
      }
      if (this._muteNode) {
        try {
          this._muteNode.disconnect();
        } catch (err) {
          console.warn("Mute node disconnect error", err);
        }
        this._muteNode = null;
      }
      if (this._sourceNode) {
        try {
          this._sourceNode.disconnect();
        } catch (err) {
          console.warn("Source disconnect error", err);
        }
        this._sourceNode = null;
      }
      if (this._audioCtx) {
        try {
          this._audioCtx.close();
        } catch (err) {
          console.warn("AudioContext close error", err);
        }
        this._audioCtx = null;
      }
      if (this._downsampler) {
        this._downsampler.reset();
        this._downsampler = null;
      }
      this._setListening(false, { resetMicEvent: true });
    },

    _setMask(active) {
      if (this._mask.active === active) return;
      if (!active && this._mask.timerId) {
        clearTimeout(this._mask.timerId);
        this._mask.timerId = null;
      }
      this._mask.active = active;
      if (active && this._downsampler) {
        this._downsampler.reset();
      }
      if (!active && this._listening) {
        this._maybeActivateCapture();
      }
    },

    _maybeActivateCapture() {
      if (!this._listening) return;
      if (!this._stream) return;
      if (this._audioCtx && typeof this._audioCtx.resume === "function") {
        this._audioCtx.resume().catch(() => {});
      }
      if (!this._micOpenEmitted) {
        this._micOpenEmitted = true;
        const eventDetail = {
          type: CLIENT_MIC_OPEN_EVENT,
          ts: Date.now(),
          vendor: this._vendor || null
        };
        emitCustomEvent(CLIENT_MIC_OPEN_EVENT, eventDetail);
        emitCustomEvent(CLIENT_HUD_STATE_EVENT, {
          type: CLIENT_HUD_STATE_EVENT,
          meta: { state: "Listening", source: "client" }
        });
      }
      if (!this._asrReady || this._mask.active) return;
      if (
        this._mediaRecorder &&
        typeof this._mediaRecorder.state === "string" &&
        this._mediaRecorder.state === "inactive"
      ) {
        try {
          this._mediaRecorder.start(WEBM_TIMESLICE_MS);
        } catch (err) {
          console.warn("MediaRecorder failed to start on capture activation", err);
        }
      }
    },

    _setListening(active, options = {}) {
      const normalized = Boolean(active);
      if (!normalized && options && options.resetMicEvent) {
        this._micOpenEmitted = false;
      }
      if (this._listening === normalized) {
        return;
      }
      this._listening = normalized;
      if (normalized) {
        this._maybeActivateCapture();
      }
    },

    _scheduleMaskRelease(delayMs) {
      if (this._mask.timerId) {
        clearTimeout(this._mask.timerId);
        this._mask.timerId = null;
      }
      if (delayMs > 0) {
        this._mask.timerId = setTimeout(() => {
          this._mask.timerId = null;
          this._setMask(false);
        }, delayMs);
      } else {
        this._setMask(false);
      }
    },

    async handleAsrReady(frame) {
      const descriptor = frame && frame.input;
      const vendor = frame && frame.vendor;
      if (!descriptor || !vendor) {
        console.warn("Invalid asr.ready frame", frame);
        return;
      }
      const normalized = {
        container: String(descriptor.container || "").toLowerCase(),
        codec: String(descriptor.codec || "").toLowerCase(),
        rate_hz: Number(descriptor.rate_hz) || 0,
        channels: Number(descriptor.channels) || 1
      };
      this._pendingDescriptor = { vendor, descriptor: normalized };
      try {
        await this.start();
      } catch (err) {
        console.error("AudioRecorder start failed for asr.ready", err);
        return;
      }
      if (this._pendingDescriptor) {
        this._activateDescriptor(this._pendingDescriptor);
        this._pendingDescriptor = null;
      }
    },

    _activateDescriptor(spec) {
      if (!spec || !this._stream) return;
      const { vendor, descriptor } = spec;
      this._teardownEncoder();
      this._vendor = vendor;
      this._descriptor = descriptor;
      if (descriptor.container === "webm" && descriptor.codec === "opus" && descriptor.rate_hz === 48000 && descriptor.channels === 1) {
        this._setupWebmRecorder();
      } else if (descriptor.container === "raw" && descriptor.codec === "pcm_s16le" && descriptor.rate_hz === 16000 && descriptor.channels === 1) {
        this._setupPcmRecorder();
      } else {
        console.error("Unsupported ASR descriptor", descriptor);
      }
      this._maybeActivateCapture();
    },

    _setupWebmRecorder() {
      if (typeof MediaRecorder === "undefined") {
        console.error("MediaRecorder not supported in this browser");
        return;
      }
      const mimeType = "audio/webm;codecs=opus";
      if (typeof MediaRecorder.isTypeSupported === "function" && !MediaRecorder.isTypeSupported(mimeType)) {
        console.error("MediaRecorder does not support", mimeType);
        return;
      }
      try {
        const recorder = new MediaRecorder(this._stream, { mimeType });
        this._handleWebmData = (event) => {
          const chunk = event.data;
          if (!chunk || !chunk.size) return;
          if (!this._canSend()) return;
          this._sendChunk(chunk);
        };
        this._handleRecorderError = (event) => {
          console.error("MediaRecorder error", event);
        };
        recorder.addEventListener("dataavailable", this._handleWebmData);
        recorder.addEventListener("error", this._handleRecorderError);
        this._mediaRecorder = recorder;
        this._asrReady = true;
        this._maybeActivateCapture();
      } catch (err) {
        console.error("Failed to start MediaRecorder", err);
      }
    },

    _setupPcmRecorder() {
      if (!AudioContextCtor) {
        console.error("Web Audio API is not supported");
        return;
      }
      try {
        const ctx = new AudioContextCtor();
        const source = ctx.createMediaStreamSource(this._stream);
        const channels = Math.max(1, source.channelCount || 1);
        const processor = ctx.createScriptProcessor(4096, channels, 1);
        const gain = ctx.createGain();
        gain.gain.value = 0;
        processor.connect(gain);
        gain.connect(ctx.destination);
        source.connect(processor);
        this._downsampler = new PcmDownsampler(ctx.sampleRate, this._descriptor.rate_hz);
        processor.onaudioprocess = (event) => {
          if (!this._canSend()) {
            if (this._downsampler) this._downsampler.reset();
            return;
          }
          const input = event.inputBuffer;
          const channelData = [];
          for (let i = 0; i < input.numberOfChannels; i += 1) {
            channelData.push(input.getChannelData(i));
          }
          const mono = downmixToMono(channelData);
          this._downsampler.push(mono);
          const int16 = this._downsampler.pullInt16();
          if (int16 && int16.byteLength) {
            this._sendChunk(int16.buffer, int16.byteLength);
          }
        };
        if (typeof ctx.resume === "function") {
          ctx.resume().catch(() => {});
        }
        this._audioCtx = ctx;
        this._sourceNode = source;
        this._processorNode = processor;
        this._muteNode = gain;
        this._asrReady = true;
        this._maybeActivateCapture();
      } catch (err) {
        console.error("Failed to set up PCM recorder", err);
      }
    },

    _canSend() {
      if (!this._asrReady || !this._stream) return false;
      if (!this._listening) return false;
      if (this._mask.active) return false;
      const WSClient = window.WSClient;
      if (!WSClient || typeof WSClient.isConnected !== "function" || typeof WSClient.sendBinary !== "function") {
        return false;
      }
      return WSClient.isConnected();
    },

    _sendChunk(payload, sizeOverride) {
      const WSClient = window.WSClient;
      if (!WSClient || typeof WSClient.sendBinary !== "function") return false;
      const bufferedAmount = typeof WSClient.getBufferedAmount === "function" ? WSClient.getBufferedAmount() : 0;
      if (bufferedAmount > BACKPRESSURE_LIMIT_BYTES) {
        console.warn("Dropping audio chunk due to backpressure", bufferedAmount);
        return false;
      }
      const sent = WSClient.sendBinary(payload, { dropIfBusy: true });
      if (sent) {
        const size = typeof sizeOverride === "number" ? sizeOverride : computePayloadSize(payload);
        console.debug("[AudioRecorder] uplink chunk bytes", size);
      } else {
        console.warn("Failed to send audio chunk");
      }
      return sent;
    },

    handlePolicy(frame) {
      const policy = frame && frame.policy;
      if (policy && typeof policy.barge_in_enabled === "boolean") {
        this._bargeInEnabled = policy.barge_in_enabled;
        if (this._bargeInEnabled && this._mask.active) {
          this._mask.holdMs = 0;
          this._setMask(false);
        }
      }
    },

    handleTtsStart(frame) {
      if (this._bargeInEnabled) return;
      const hold = Number(frame && frame.post_hold_ms);
      this._mask.holdMs = Number.isFinite(hold) && hold > 0 ? hold : 0;
      this._setMask(true);
      this._setListening(false, { resetMicEvent: true });
    },

    handleTtsEnd() {
      if (!this._mask.active) return;
      const hold = Math.max(this._mask.holdMs || 0, POST_TTS_RELEASE_DELAY_MS);
      this._mask.holdMs = 0;
      this._scheduleMaskRelease(hold);
    },

    async startListening() {
      try {
        await this.start();
      } catch (err) {
        console.error("AudioRecorder start_listening start failed", err);
        return;
      }
      this._asrReady = true;
      this._setMask(false);
      this._setListening(true);
    },

    async handleStartListening(frame) {
      return this.startListening(frame);
    },

    handleWsClose() {
      this._teardownEncoder();
      this._asrReady = false;
      this._descriptor = null;
      this._pendingDescriptor = null;
      this._vendor = null;
      this._setMask(false);
      this._setListening(false, { resetMicEvent: true });
    }
  };

  window.addEventListener("policy.interaction", (event) => {
    recorder.handlePolicy(event && event.detail);
  });
  window.addEventListener("tts.start", (event) => {
    recorder.handleTtsStart(event && event.detail);
  });
  window.addEventListener("tts.end", () => {
    recorder.handleTtsEnd();
  });
  window.addEventListener("ws.close", () => {
    recorder.handleWsClose();
  });

  window.AudioRecorder = recorder;
})();
