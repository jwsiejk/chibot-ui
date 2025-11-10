(() => {
  const TARGET_SAMPLE_RATE = 16000;
  const MIN_FRAME_SAMPLES = 320;
  const MAX_FRAME_SAMPLES = 640;
  const DEFAULT_CHUNK_MS = Math.round((MIN_FRAME_SAMPLES / TARGET_SAMPLE_RATE) * 1000);

  const BUILD_ID = typeof window !== 'undefined' && typeof window.BUILD_ID === 'string'
    ? window.BUILD_ID
    : null;

  const CACHE_STAMP = (() => {
    if (typeof BUILD_ID === 'string' && BUILD_ID) {
      return BUILD_ID;
    }
    if (typeof window !== 'undefined') {
      const existing = window.__PCM_WORKLET_STAMP__;
      if (typeof existing === 'string' && existing) {
        return existing;
      }
      const generated = Date.now().toString();
      try {
        window.__PCM_WORKLET_STAMP__ = generated;
      } catch (err) {
        /* ignore assignment errors */
      }
      return generated;
    }
    return Date.now().toString();
  })();

  function withCacheBuster(path) {
    if (!path || typeof path !== 'string') {
      return path;
    }
    const hashIndex = path.indexOf('#');
    const base = hashIndex === -1 ? path : path.slice(0, hashIndex);
    const hash = hashIndex === -1 ? '' : path.slice(hashIndex);
    const sep = base.includes('?') ? '&' : '?';
    return `${base}${sep}v=${encodeURIComponent(CACHE_STAMP)}${hash}`;
  }

  const WORKLET_PATH = withCacheBuster('/static/js/audio/pcm-worklet-processor.js');

  function emitClientLog(label, detail = {}) {
    const payload = detail && typeof detail === 'object' ? detail : { value: detail };
    const AppState = typeof window !== 'undefined' ? window.AppState : null;
    if (AppState && AppState.hub && typeof AppState.hub.log === 'function') {
      try {
        AppState.hub.log(label, payload);
        return;
      } catch (err) {
        try {
          console.warn('client.log hub emit failed', err);
        } catch (_) {}
      }
    }
    if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
      try {
        window.dispatchEvent(new CustomEvent('client.log', { detail: { label, detail: payload } }));
      } catch (err) {
        try {
          console.warn('client.log dispatch failed', err);
        } catch (_) {}
      }
    }
  }

  function logClientMicEventText(text) {
    if (typeof text !== 'string' || !text) {
      return;
    }
    const win = typeof window !== 'undefined' ? window : null;
    if (win && typeof win.__logClientMicString === 'function') {
      try {
        win.__logClientMicString(text);
        return;
      } catch (_) {}
    }
    emitClientLog('client.mic', { message: text });
  }

  function publishClientVad(next = {}) {
    if (typeof window === 'undefined') {
      return;
    }
    try {
      const win = window;
      const appState = win.AppState = win.AppState || {};
      const storeState = appState.state = appState.state || {};
      const patch = {};
      const nestedStatePatch = {};

      if (typeof next.vadActive === 'boolean') {
        if (storeState.vadActive !== next.vadActive) {
          patch.vadActive = next.vadActive;
          nestedStatePatch.vadActive = next.vadActive;
        }
        storeState.vadActive = next.vadActive;
      }

      if (Object.prototype.hasOwnProperty.call(next, 'vadDbfs')) {
        const normalizedDb = Number.isFinite(next.vadDbfs) ? next.vadDbfs : null;
        if (!Object.is(storeState.vadDbfs, normalizedDb)) {
          patch.vadEnergyDb = normalizedDb;
          nestedStatePatch.vadDbfs = normalizedDb;
        }
        storeState.vadDbfs = normalizedDb;
      }

      if (Object.prototype.hasOwnProperty.call(next, 'lastSpeechAt')) {
        const normalizedLastSpeech = Number.isFinite(next.lastSpeechAt) ? next.lastSpeechAt : null;
        if (!Object.is(storeState.lastSpeechAt, normalizedLastSpeech)) {
          patch.clientVadLastSpeechAt = normalizedLastSpeech;
          nestedStatePatch.lastSpeechAt = normalizedLastSpeech;
        }
        storeState.lastSpeechAt = normalizedLastSpeech;
      }

      if (appState && typeof appState.setState === 'function') {
        const keys = Object.keys(patch);
        const nestedStateKeys = Object.keys(nestedStatePatch);
        if (keys.length || nestedStateKeys.length) {
          const payload = {};
          for (const key of keys) {
            payload[key] = patch[key];
          }
          if (nestedStateKeys.length) {
            payload.state = {};
            for (const key of nestedStateKeys) {
              payload.state[key] = nestedStatePatch[key];
            }
          }
          appState.setState(payload);
        }
      }
    } catch (err) {
      try {
        console.warn('publishClientVad failed', err);
      } catch (_) {}
    }
  }

  function sanitizeLogText(value, fallback = 'unknown', maxLength = 160) {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) {
        if (trimmed.length > maxLength) {
          return `${trimmed.slice(0, maxLength - 1)}…`;
        }
        return trimmed;
      }
    }
    if (value == null) {
      return fallback;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    try {
      const serialized = JSON.stringify(value);
      if (typeof serialized === 'string' && serialized) {
        if (serialized.length > maxLength) {
          return `${serialized.slice(0, maxLength - 1)}…`;
        }
        return serialized;
      }
    } catch (_) {}
    try {
      const text = String(value);
      if (text.length > maxLength) {
        return `${text.slice(0, maxLength - 1)}…`;
      }
      return text;
    } catch (_) {}
    return fallback;
  }

  function serializeConstraintsForLog(constraints) {
    try {
      const json = JSON.stringify(constraints);
      if (typeof json === 'string' && json) {
        if (json.length > 512) {
          return `${json.slice(0, 511)}…`;
        }
        return json;
      }
    } catch (_) {}
    return '"unserializable"';
  }

  let __pcmRecorderGumInFlight = false;
  async function requestUserMediaOnce(constraints) {
    const win = typeof window !== 'undefined' ? window : null;
    if (win && typeof win.getMicOnce === 'function') {
      return win.getMicOnce(constraints);
    }
    if (__pcmRecorderGumInFlight) {
      return null;
    }
    __pcmRecorderGumInFlight = true;
    try {
      if (!navigator?.mediaDevices?.getUserMedia) {
        throw new Error('MediaDevices.getUserMedia is not available');
      }
      return await navigator.mediaDevices.getUserMedia(constraints);
    } finally {
      __pcmRecorderGumInFlight = false;
    }
  }

  function normalizeReason(reason) {
    if (typeof reason === 'string' && reason) {
      return reason;
    }
    if (reason && typeof reason === 'object') {
      if (typeof reason.reason === 'string' && reason.reason) {
        return reason.reason;
      }
      if (typeof reason.code === 'string' && reason.code) {
        return reason.code;
      }
      if (typeof reason.message === 'string' && reason.message) {
        return reason.message;
      }
    }
    return 'unknown';
  }

  function emitAppStateRecordingStarted(policy) {
    const AppState = typeof window !== 'undefined' ? window.AppState : null;
    if (!AppState || typeof AppState.emit !== 'function') {
      return;
    }
    try {
      AppState.emit('recordingStarted', { policy: policy || null, source: 'pcm_recorder' });
    } catch (err) {
      try {
        console.warn('recordingStarted emit failed', err);
      } catch (_) {}
    }
  }

  class PcmRecorder {
    constructor() {
      this._audioContext = null;
      this._sourceNode = null;
      this._workletNode = null;
      this._stream = null;
      this._socket = null;
      this._active = false;
      this._listening = false;
      this._muted = false;
      this._chunkSeq = 0;
      this._listeningStartedAt = 0;
      this._firstChunkSent = false;
      this._policy = {};
      this._shouldMuteOnTts = true;
      this._stopOnTts = false;
      this._onChunk = null;
      this._deviceSampleRate = null;
      this._initializing = null;
      this._workletLoaded = false;
      this._tearDownPromise = null;
      this._clientVadConfig = this._extractClientVadConfig({});
      this._clientVadState = this._createClientVadState();
      this._startPromise = null;
    }

    get listening() {
      return this._listening;
    }

    isActive() {
      return this._active;
    }

    isListening() {
      return this._listening;
    }

    async init() {
      if (this._initializing) {
        return this._initializing;
      }
      if (this._audioContext && this._audioContext.state === 'closed') {
        this._audioContext = null;
        this._workletLoaded = false;
      }
      if (this._audioContext && this._workletLoaded) {
        return this._audioContext;
      }

      const initializer = (async () => {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) {
          throw new Error('Web Audio API is not supported');
        }
        const context = new AudioContextCtor();
        this._audioContext = context;
        try {
          window.__audioCtx = context;
        } catch (_) {}
        this._deviceSampleRate = context.sampleRate || null;
        if (!context.audioWorklet || typeof context.audioWorklet.addModule !== 'function') {
          throw new Error('AudioWorklet is not supported');
        }
        try {
          await context.audioWorklet.addModule(WORKLET_PATH);
          this._workletLoaded = true;
          emitClientLog('client.audio.worklet_load_ok', {
            sample_rate: this._deviceSampleRate,
            target_rate: TARGET_SAMPLE_RATE,
            url: WORKLET_PATH,
          });
        } catch (err) {
          emitClientLog('client.audio.worklet_load_fail', {
            message: err && err.message ? err.message : String(err),
          });
          try {
            context.close();
          } catch (_) {}
          this._audioContext = null;
          this._workletLoaded = false;
          throw err;
        }
        return context;
      })();

      this._initializing = initializer
        .catch((err) => {
          this._initializing = null;
          throw err;
        })
        .then((ctx) => {
          this._initializing = null;
          return ctx;
        });

      return this._initializing;
    }

    async start(options = {}) {
      const config = (options && typeof options === 'object') ? options : {};
      if (typeof config.onChunk === 'function') {
        this._onChunk = config.onChunk;
      }
      const policyCandidate = this._extractPolicyFromConfig(config);
      if (policyCandidate) {
        this.setPolicy(policyCandidate);
      }
      if (this._active) {
        return this._audioContext;
      }
      if (this._startPromise) {
        return this._startPromise;
      }
      if (!navigator?.mediaDevices?.getUserMedia) {
        throw new Error('MediaDevices.getUserMedia is not available');
      }

      const startOperation = (async () => {
        const context = await this.init();
        if (!context) {
          throw new Error('Audio context unavailable');
        }

        if (context.state === 'suspended') {
          try {
            await context.resume();
          } catch (err) {
            console.warn('Failed to resume audio context', err);
          }
        }

        const constraintSource = this._policy
          && typeof this._policy === 'object'
          && this._policy.capture
          && typeof this._policy.capture === 'object'
          && this._policy.capture.constraints
          && typeof this._policy.capture.constraints === 'object'
            ? this._policy.capture.constraints
            : {};

        const audioConstraints = {};
        Object.keys(constraintSource).forEach((key) => {
          const value = constraintSource[key];
          if (typeof value !== 'undefined') {
            audioConstraints[key] = value;
          }
        });

        if (typeof audioConstraints.channelCount === 'undefined') {
          audioConstraints.channelCount = 1;
        } else if (Number.isFinite(audioConstraints.channelCount)) {
          audioConstraints.channelCount = Number(audioConstraints.channelCount);
        }

        const sampleRateSource = audioConstraints.sampleRate;
        if (typeof sampleRateSource === 'undefined') {
          audioConstraints.sampleRate = { ideal: 48000 };
        } else if (Number.isFinite(sampleRateSource)) {
          audioConstraints.sampleRate = { ideal: Number(sampleRateSource) };
        } else if (sampleRateSource && typeof sampleRateSource === 'object') {
          audioConstraints.sampleRate = { ...sampleRateSource };
        }

        if (typeof audioConstraints.sampleSize === 'undefined') {
          audioConstraints.sampleSize = 16;
        } else if (Number.isFinite(audioConstraints.sampleSize)) {
          audioConstraints.sampleSize = Number(audioConstraints.sampleSize);
        }

        if (typeof audioConstraints.echoCancellation === 'undefined') {
          audioConstraints.echoCancellation = true;
        } else {
          audioConstraints.echoCancellation = Boolean(audioConstraints.echoCancellation);
        }

        if (typeof audioConstraints.noiseSuppression === 'undefined') {
          audioConstraints.noiseSuppression = true;
        } else {
          audioConstraints.noiseSuppression = Boolean(audioConstraints.noiseSuppression);
        }

        let runtimeAgc = null;
        if (typeof window !== 'undefined') {
          try {
            const mediaPolicy = window.AppState?.policy?.media;
            if (mediaPolicy && Object.prototype.hasOwnProperty.call(mediaPolicy, 'agc')) {
              runtimeAgc = Boolean(mediaPolicy.agc);
            }
          } catch (_) {}
        }
        if (runtimeAgc !== null) {
          audioConstraints.autoGainControl = runtimeAgc;
        } else if (typeof audioConstraints.autoGainControl === 'undefined') {
          audioConstraints.autoGainControl = false;
        } else {
          audioConstraints.autoGainControl = Boolean(audioConstraints.autoGainControl);
        }

        let stream = this._stream;
        if (!stream) {
          try {
            const constraintsLog = serializeConstraintsForLog(audioConstraints);
            logClientMicEventText(`evt=mic_get_user_media_start constraints=${constraintsLog}`);
            stream = await requestUserMediaOnce({
              audio: audioConstraints,
              video: false,
            });
            if (!stream) {
              logClientMicEventText('evt=mic_get_user_media_skip reason=in_flight');
              const err = new Error('mic_capture_in_flight');
              err.code = 'mic_capture_in_flight';
              throw err;
            }
            const audioTracks = stream && typeof stream.getAudioTracks === 'function'
              ? stream.getAudioTracks()
              : [];
            const primaryTrack = audioTracks && audioTracks.length ? audioTracks[0] : null;
            const trackId = primaryTrack && typeof primaryTrack.id === 'string' && primaryTrack.id
              ? primaryTrack.id
              : 'unknown';
            const safeTrackId = sanitizeLogText(trackId, 'unknown', 80);
            logClientMicEventText(`evt=mic_get_user_media_success track_id=${safeTrackId}`);
          } catch (err) {
            const errorName = sanitizeLogText(err && err.name ? err.name : null, 'unknown', 64);
            const errorMessageSource = err && typeof err.message === 'string' && err.message
              ? err.message
              : err;
            const errorMessage = sanitizeLogText(errorMessageSource, 'unknown', 160);
            logClientMicEventText(`evt=mic_get_user_media_fail error=${errorName} message=${errorMessage}`);
            emitClientLog('client.pcm.capture_error', {
              stage: 'getUserMedia',
              message: err && err.message ? err.message : String(err),
            });
            throw err;
          }
        }

        const sourceNode = context.createMediaStreamSource(stream);
        let workletNode;
        try {
          workletNode = new AudioWorkletNode(context, 'pcm-worklet-processor', {
            numberOfOutputs: 0,
            processorOptions: {
              targetSampleRate: TARGET_SAMPLE_RATE,
              minFrameSamples: MIN_FRAME_SAMPLES,
              maxFrameSamples: MAX_FRAME_SAMPLES,
            },
          });
        } catch (err) {
          emitClientLog('client.pcm.capture_error', {
            stage: 'createWorkletNode',
            message: err && err.message ? err.message : String(err),
          });
          stream.getTracks().forEach((track) => {
            try { track.stop(); } catch (_) {}
          });
          throw err;
        }

        this._attachPort(workletNode.port);
        try {
          sourceNode.connect(workletNode);
        } catch (err) {
          emitClientLog('client.pcm.capture_error', {
            stage: 'connectWorklet',
            message: err && err.message ? err.message : String(err),
          });
          workletNode.port.onmessage = null;
          try { workletNode.disconnect(); } catch (_) {}
          try { sourceNode.disconnect(); } catch (_) {}
          stream.getTracks().forEach((track) => {
            try { track.stop(); } catch (_) {}
          });
          throw err;
        }

        this._stream = stream;
        this._sourceNode = sourceNode;
        this._workletNode = workletNode;
        this._active = true;
        this._muted = false;
        this._firstChunkSent = false;
        return context;
      })();

      const guardedStart = startOperation.finally(() => {
        if (this._startPromise === guardedStart || this._startPromise === startOperation) {
          this._startPromise = null;
        }
      });
      this._startPromise = guardedStart;
      return guardedStart;
    }

    async startListening(policy = {}) {
      if (policy && typeof policy === 'object' && Object.keys(policy).length) {
        this.setPolicy(policy);
      }
      if (!this._active) {
        await this.start(policy || {});
      }
      if (this._listening) {
        return;
      }
      this._listening = true;
      this._chunkSeq = 0;
      this._firstChunkSent = false;
      this._listeningStartedAt = Date.now();
      this._resetClientVadState();
      const vadConfig = this._getResolvedClientVadConfig();
      const chunkMs = DEFAULT_CHUNK_MS;
      emitClientLog('client.pcm.capture_start', {
        device_rate: this._deviceSampleRate || null,
        target_rate: TARGET_SAMPLE_RATE,
        chunk_ms: chunkMs,
      });
      emitClientLog('client.vad.params', {
        enable: vadConfig.enable,
        threshold_dbfs: vadConfig.thresholdDbfs,
        attack_ms: vadConfig.attackMs,
        release_ms: vadConfig.releaseMs,
        pre_roll_ms: vadConfig.preRollMs,
        min_active_ms: vadConfig.minActiveMs,
      });
      emitAppStateRecordingStarted(this._policy);
    }

    stopListening(reason = {}) {
      if (!this._listening) {
        return;
      }
      const reasonLabel = normalizeReason(reason);
      this._listening = false;
      this._muted = false;
      this._resetClientVadState();
      emitClientLog('client.pcm.capture_stop', { reason: reasonLabel });
    }

    async stop(reason = {}) {
      this.stopListening(reason);
      if (!this._active) {
        return;
      }
      if (this._tearDownPromise) {
        return this._tearDownPromise;
      }
      const cleanup = (async () => {
        const stream = this._stream;
        const source = this._sourceNode;
        const worklet = this._workletNode;
        this._stream = null;
        this._sourceNode = null;
        this._workletNode = null;
        this._active = false;
        this._muted = false;
        this._onChunk = null;
        if (worklet && worklet.port) {
          try {
            worklet.port.onmessage = null;
          } catch (_) {}
        }
        try {
          if (source) {
            source.disconnect();
          }
        } catch (_) {}
        try {
          if (worklet) {
            worklet.disconnect();
          }
        } catch (_) {}
        if (stream) {
          stream.getTracks().forEach((track) => {
            try { track.stop(); } catch (_) {}
          });
        }
        if (this._audioContext && this._audioContext.state === 'running') {
          try {
            await this._audioContext.suspend();
          } catch (err) {
            console.warn('AudioContext suspend failed', err);
          }
        }
      })();

      this._tearDownPromise = cleanup
        .catch((err) => {
          this._tearDownPromise = null;
          throw err;
        })
        .then((value) => {
          this._tearDownPromise = null;
          return value;
        });

      return this._tearDownPromise;
    }

    setSocket(ws) {
      this._socket = ws || null;
    }

    setPolicy(policy) {
      if (!policy || typeof policy !== 'object') {
        this._policy = {};
        this._applyPolicyDefaults();
        return;
      }
      this._policy = { ...policy };
      this._applyPolicyDefaults();
    }

    handleTtsStart() {
      if (this._stopOnTts) {
        this.stopListening({ reason: 'tts_start_policy' });
        return;
      }
      if (this._shouldMuteOnTts) {
        this._muted = true;
      }
    }

    handleTtsEnd() {
      this._muted = false;
    }

    startMicCaptureIfIdle(options = {}) {
      if (this._listening || this._active) {
        logClientMicEventText('evt=mic_capture_request_blocked reason=active');
        return Promise.resolve(true);
      }
      if (this._startPromise) {
        logClientMicEventText('evt=mic_capture_request_blocked reason=starting');
        return this._startPromise.then(() => true);
      }
      if (this._initializing) {
        logClientMicEventText('evt=mic_capture_request_blocked reason=initializing');
      }
      let outcome;
      try {
        outcome = this.start(options);
      } catch (err) {
        return Promise.reject(err);
      }
      if (outcome && typeof outcome.then === 'function') {
        return outcome.then(() => true);
      }
      return Promise.resolve(Boolean(outcome));
    }

    _attachPort(port) {
      if (!port) {
        return;
      }
      port.onmessage = (event) => {
        const data = event ? event.data : null;
        if (!data || data.type !== 'pcm16' || !data.buffer) {
          return;
        }
        const buffer = data.buffer;
        const byteLength = buffer.byteLength || 0;
        if (byteLength === 0) {
          return;
        }
        if (!this._listening || this._muted) {
          return;
        }
        this._handleIncomingChunk(buffer);
      };
    }

    _handleIncomingChunk(buffer) {
      const byteLength = buffer && buffer.byteLength ? buffer.byteLength : 0;
      if (!byteLength) {
        return;
      }

      const cfg = this._getResolvedClientVadConfig();
      if (!cfg.enable) {
        this._dispatchChunk(buffer);
        return;
      }

      let state = this._clientVadState;
      if (!state) {
        state = this._createClientVadState();
        this._clientVadState = state;
      }

      const now = Date.now();
      const dbfs = this._clientVadRmsDbfs(buffer);
      const chunkMs = this._clientVadEstimateChunkMs(buffer);
      const threshold = cfg.thresholdDbfs;
      const above = dbfs >= threshold;
      const normalizedDb = Number.isFinite(dbfs) ? dbfs : null;
      publishClientVad({ vadActive: Boolean(state.active), vadDbfs: normalizedDb });
      // meter: log a few samples until first activation
      if (!state.meterDone) {
        state.meterSamples = (state.meterSamples || 0) + 1;
        if (state.meterSamples % 5 === 0) {
          try {
            emitClientLog('client.vad.meter', { dbfs, above, threshold });
          } catch (_) {}
          if (state.active) {
            state.meterDone = true;
          }
        }
      }
      const maxPrerollChunks = cfg.preRollMs <= 0
        ? 0
        : Math.max(1, Math.round(cfg.preRollMs / Math.max(chunkMs, 1)));

      if (!state.active) {
        let shouldActivate = false;
        if (above) {
          if (!state.aboveSince) {
            state.aboveSince = now;
          }
          if ((now - state.aboveSince) >= cfg.attackMs) {
            shouldActivate = true;
          }
        } else {
          state.aboveSince = 0;
        }

        if (shouldActivate) {
          state.active = true;
          state.activeSince = now;
          state.belowSince = 0;
          state.aboveSince = now;
          state.meterDone = true;
          const flushed = this._clientVadFlushPreroll(state);
          if (flushed > 0) {
            try {
              emitClientLog('client.vad.preroll_flush', { chunks: flushed });
            } catch (_) {}
          }
          try {
            emitClientLog('client.vad.state', { state: 'active', dbfs });
          } catch (_) {}
          publishClientVad({ vadActive: true, vadDbfs: normalizedDb, lastSpeechAt: now });
          this._dispatchChunk(buffer);
          return;
        }

        if (maxPrerollChunks > 0) {
          state.preroll.push(buffer.slice(0));
          while (state.preroll.length > maxPrerollChunks) {
            state.preroll.shift();
          }
        } else if (state.preroll.length) {
          state.preroll = [];
        }
        return;
      }

      this._dispatchChunk(buffer);
      publishClientVad({ vadActive: true, vadDbfs: normalizedDb, ...(above ? { lastSpeechAt: now } : {}) });
      if (above) {
        state.belowSince = 0;
        return;
      }

      if (!state.belowSince) {
        state.belowSince = now;
      }
      const activeMs = state.activeSince ? Math.max(0, now - state.activeSince) : 0;
      const belowMs = Math.max(0, now - state.belowSince);
      if (belowMs >= cfg.releaseMs && activeMs >= cfg.minActiveMs) {
        state.active = false;
        state.aboveSince = 0;
        state.belowSince = 0;
        state.activeSince = 0;
        state.preroll = [];
        try {
          emitClientLog('client.vad.state', { state: 'paused', dbfs });
        } catch (_) {}
        publishClientVad({ vadActive: false, vadDbfs: normalizedDb });
      }
    }

    _dispatchChunk(buffer) {
      const byteLength = buffer && buffer.byteLength ? buffer.byteLength : 0;
      if (!byteLength) {
        return false;
      }
      const seq = this._chunkSeq;
      this._chunkSeq += 1;
      if (!this._firstChunkSent) {
        this._firstChunkSent = true;
        emitClientLog('client.audio_first_chunk', {
          bytes: byteLength,
          seq,
        });
      }
      const startedAt = this._listeningStartedAt || Date.now();
      const msSinceStart = Math.max(0, Date.now() - startedAt);
      emitClientLog('client.pcm.chunk', {
        seq,
        bytes: byteLength,
        ms_since_start: msSinceStart,
      });
      const delivered = this._deliverChunk(buffer, seq);
      if (!delivered) {
        emitClientLog('client.pcm.chunk_drop', {
          seq,
          bytes: byteLength,
          reason: 'delivery_failed',
        });
      }
      return delivered;
    }

    _getResolvedClientVadConfig() {
      const defaults = {
        enable: true,
        thresholdDbfs: -60,
        attackMs: 80,
        releaseMs: 250,
        preRollMs: 240,
        minActiveMs: 300,
      };

      let cfg = this._clientVadConfig || this._extractClientVadConfig({}) || {};
      if (!cfg || typeof cfg !== 'object') {
        cfg = {};
      }

      const toFiniteNumber = (value) => {
        if (Number.isFinite(value)) {
          return value;
        }
        if (typeof value === 'string' && value.trim()) {
          const parsed = Number(value);
          if (Number.isFinite(parsed)) {
            return parsed;
          }
        }
        return null;
      };

      const pickNumber = (fallback, ...candidates) => {
        for (let i = 0; i < candidates.length; i += 1) {
          const candidate = toFiniteNumber(candidates[i]);
          if (candidate !== null) {
            return candidate;
          }
        }
        return fallback;
      };

      const thresholdDbfs = pickNumber(defaults.thresholdDbfs, cfg.thresholdDbfs, cfg.threshold_dbfs);
      const attackMs = Math.max(0, pickNumber(defaults.attackMs, cfg.attackMs, cfg.attack_ms));
      const releaseMs = Math.max(0, pickNumber(defaults.releaseMs, cfg.releaseMs, cfg.release_ms));
      const preRollMs = Math.max(0, pickNumber(defaults.preRollMs, cfg.preRollMs, cfg.pre_roll_ms));
      const minActiveMs = Math.max(0, pickNumber(defaults.minActiveMs, cfg.minActiveMs, cfg.min_active_ms));

      return {
        enable: cfg.enable !== false,
        thresholdDbfs,
        attackMs,
        releaseMs,
        preRollMs,
        minActiveMs,
      };
    }

    _clientVadEstimateChunkMs(buffer) {
      if (!buffer || !buffer.byteLength) {
        return DEFAULT_CHUNK_MS;
      }
      const samples = buffer.byteLength / 2;
      if (!samples || !Number.isFinite(samples)) {
        return DEFAULT_CHUNK_MS;
      }
      return (samples / TARGET_SAMPLE_RATE) * 1000;
    }

    _clientVadRmsDbfs(buffer) {
      if (!buffer || buffer.byteLength < 2) {
        return -Infinity;
      }
      let view;
      try {
        view = new Int16Array(buffer);
      } catch (_) {
        return -Infinity;
      }
      const length = view.length || 0;
      if (!length) {
        return -Infinity;
      }
      let sumSquares = 0;
      for (let i = 0; i < length; i += 1) {
        const sample = view[i] / 32768;
        sumSquares += sample * sample;
      }
      if (sumSquares <= 0) {
        return -Infinity;
      }
      const mean = sumSquares / length;
      if (mean <= 0) {
        return -Infinity;
      }
      const rms = Math.sqrt(mean);
      if (rms <= 1e-9) {
        return -Infinity;
      }
      return 20 * Math.log10(rms);
    }

    _clientVadFlushPreroll(state) {
      if (!state || !Array.isArray(state.preroll) || state.preroll.length === 0) {
        state.preroll = [];
        return 0;
      }
      let sent = 0;
      for (let i = 0; i < state.preroll.length; i += 1) {
        const chunk = state.preroll[i];
        if (chunk && chunk.byteLength) {
          this._dispatchChunk(chunk);
          sent += 1;
        }
      }
      state.preroll = [];
      return sent;
    }

    _createClientVadState() {
      return {
        active: false,
        aboveSince: 0,
        belowSince: 0,
        activeSince: 0,
        preroll: [],
        meterSamples: 0,
        meterDone: false,
      };
    }

    _resetClientVadState() {
      this._clientVadState = this._createClientVadState();
    }

    _deliverChunk(buffer, seq) {
      let delivered = false;
      if (typeof this._onChunk === 'function') {
        try {
          this._onChunk({ buffer, seq });
          delivered = true;
        } catch (err) {
          emitClientLog('client.pcm.chunk_callback_error', {
            seq,
            message: err && err.message ? err.message : String(err),
          });
        }
      }

      if (!delivered) {
        delivered = this._sendViaSocket(buffer, seq);
      }

      return delivered;
    }

    _sendViaSocket(buffer, seq) {
      const byteLength = buffer.byteLength || 0;
      if (!byteLength) {
        return true;
      }
      const wsClient = typeof window !== 'undefined' ? window.WSClient : null;
      if (wsClient && typeof wsClient.sendAudioChunk === 'function') {
        try {
          wsClient.sendAudioChunk(buffer);
          return true;
        } catch (err) {
          emitClientLog('client.pcm.send_error', {
            seq,
            message: err && err.message ? err.message : String(err),
            path: 'wsclient.sendAudioChunk',
          });
        }
      } else if (wsClient && typeof wsClient.sendBinary === 'function') {
        try {
          wsClient.sendBinary(buffer, { lane: 'mic' });
          return true;
        } catch (err) {
          emitClientLog('client.pcm.send_error', {
            seq,
            message: err && err.message ? err.message : String(err),
            path: 'wsclient.sendBinary',
          });
        }
      } else if (wsClient && typeof wsClient.send === 'function') {
        try {
          wsClient.send(buffer, { binary: true });
          return true;
        } catch (err) {
          emitClientLog('client.pcm.send_error', {
            seq,
            message: err && err.message ? err.message : String(err),
            path: 'wsclient.send',
          });
        }
      }
      const socket = this._socket;
      if (socket && socket.readyState === WebSocket.OPEN) {
        try {
          socket.send(buffer);
          return true;
        } catch (err) {
          emitClientLog('client.pcm.send_error', {
            seq,
            message: err && err.message ? err.message : String(err),
            path: 'socket.send',
          });
        }
      }
      return false;
    }

    _applyPolicyDefaults() {
      const recorderPolicy = this._extractRecorderPolicy(this._policy);
      if (recorderPolicy && typeof recorderPolicy === 'object') {
        this._shouldMuteOnTts = recorderPolicy.mute_send_during_tts !== false;
        this._stopOnTts = recorderPolicy.stop_on_tts_start === true;
      } else {
        this._shouldMuteOnTts = true;
        this._stopOnTts = false;
      }
      this._clientVadConfig = this._extractClientVadConfig(this._policy);
      this._resetClientVadState();
    }

    _extractRecorderPolicy(policy) {
      if (!policy || typeof policy !== 'object') {
        return null;
      }
      if (policy.recorder && typeof policy.recorder === 'object') {
        return policy.recorder;
      }
      if (policy.policy && typeof policy.policy === 'object' && policy.policy.recorder) {
        return policy.policy.recorder;
      }
      return null;
    }

    _extractClientVadConfig(policy) {
      if (!policy || typeof policy !== 'object') {
        return {};
      }
      let vadPolicy = null;
      if (policy.vad && typeof policy.vad === 'object') {
        vadPolicy = policy.vad;
      } else if (policy.policy && typeof policy.policy === 'object' && policy.policy.vad && typeof policy.policy.vad === 'object') {
        vadPolicy = policy.policy.vad;
      }
      const clientCfg = vadPolicy && typeof vadPolicy.client === 'object' ? vadPolicy.client : null;
      if (!clientCfg || typeof clientCfg !== 'object') {
        return {};
      }
      try {
        return { ...clientCfg };
      } catch (_) {
        const clone = {};
        Object.keys(clientCfg).forEach((key) => {
          clone[key] = clientCfg[key];
        });
        return clone;
      }
    }

    _extractPolicyFromConfig(config) {
      if (!config || typeof config !== 'object') {
        return null;
      }
      if (config.policy && typeof config.policy === 'object') {
        return config.policy;
      }
      const knownKeys = new Set(['onChunk']);
      const hasOnlyPolicyKeys = Object.keys(config).every((key) => knownKeys.has(key));
      if (!hasOnlyPolicyKeys) {
        return config;
      }
      return null;
    }
  }

  const recorder = new PcmRecorder();
  if (typeof window !== 'undefined') {
    window.AudioRecorder = recorder;
  }

  // Idempotent preflight: ensure we hold a live mic track without starting the send loop.
  // Returns true if mic is available (existing or newly acquired).
  recorder.startMicCaptureIfIdle = async function startMicCaptureIfIdle() {
    try {
      if (this._stream && this._stream.active) return true;
      // Reuse the normal start path but with a no-op onChunk; we only want the track.
      const ok = await this.start({ onChunk: () => {}, policy: {} });
      // Optional: immediately pause internal read loop if exposed, send path is controlled by ws_client
      if (ok && typeof this.pause === 'function') this.pause();
      return !!ok;
    } catch (_) {
      return false;
    }
  };
})();
