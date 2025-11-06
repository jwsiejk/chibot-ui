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
      if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
        throw new Error('MediaDevices.getUserMedia is not available');
      }

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

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: { ideal: 1, max: 2 },
            sampleRate: { ideal: 48000 },
            sampleSize: 16,
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
          video: false,
        });
      } catch (err) {
        emitClientLog('client.pcm.capture_error', {
          stage: 'getUserMedia',
          message: err && err.message ? err.message : String(err),
        });
        throw err;
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
      const chunkMs = DEFAULT_CHUNK_MS;
      emitClientLog('client.pcm.capture_start', {
        device_rate: this._deviceSampleRate || null,
        target_rate: TARGET_SAMPLE_RATE,
        chunk_ms: chunkMs,
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

    startMicCaptureIfIdle() {
      if (this._active || this._initializing) {
        return this._initializing || Promise.resolve(null);
      }
      return this.start();
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
      };
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
      if (wsClient && typeof wsClient.send === 'function') {
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
})();
