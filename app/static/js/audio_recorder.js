import { WakeWord } from "./wake_word.js";

/** POLICY: MediaRecorder only in audio_recorder.js; no PTT; no manual barge-in; wake-word only. */
(() => {
  const SEND_TIMESLICE_MS = 300;
  const OPUS_MIME = "audio/webm;codecs=opus";
  const PCM_SAMPLE_RATE = 16000;
  const PCM_FRAME_SAMPLES = Math.round(PCM_SAMPLE_RATE * 0.05);
  const PCM_TELEMETRY_INTERVAL_MS = 1000;
  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";

  function currentInputDeviceSummary(stream) {
    if (!stream) {
      return null;
    }
    try {
      const tracks = typeof stream.getAudioTracks === "function" ? stream.getAudioTracks() : [];
      const track = Array.isArray(tracks) && tracks.length ? tracks[0] : null;
      if (!track) {
        return null;
      }
      const summary = {};
      const settings = typeof track.getSettings === "function" ? track.getSettings() : {};
      if (settings && typeof settings.deviceId === "string" && settings.deviceId) {
        summary.id = settings.deviceId;
      }
      if (typeof track.label === "string" && track.label) {
        summary.label = track.label;
      }
      if (Number.isFinite(settings.sampleRate)) {
        summary.sample_rate = settings.sampleRate;
      }
      if (Number.isFinite(settings.channelCount)) {
        summary.channels = settings.channelCount;
      }
      return Object.keys(summary).length ? summary : null;
    } catch (err) {
      console.warn("AudioRecorder device summary failed", err);
      return null;
    }
  }

  function emitEvent(type, detail) {
    try {
      window.dispatchEvent(new CustomEvent(type, { detail }));
    } catch (err) {
      console.warn("AudioRecorder event dispatch failed", type, err);
    }
  }

  class AudioRecorder {
    constructor(ws, appState) {
      this._ws = ws || null;
      this._state = appState || null;
      this._stream = null;
      this._rec = null;
      this._sendGate = false;
      this._policy = {};
      this._micOpenEmitted = false;
      this._active = false;
      this._wakeInit = false;
      this._sendMuted = false;
      this._headerSent = false;
      this._usePCM = false;
      this._audioContext = null;
      this._pcmSource = null;
      this._pcmNode = null;
      this._pcmGainNode = null;
      this._pcmResampleBuffer = [];
      this._pcmOutputBuffer = [];
      this._pcmResampleCursor = 0;
      this._pcmFrameSeq = 0;
      this._pcmLastTelemetryAt = 0;
      this._pcmUtteranceStartAt = null;
      this._pcmLastVoiceAt = null;
      this._pcmSilenceStartAt = null;
      this._pcmCommitSent = false;
      this._pcmWorkletLoaded = false;
    }

    setSocket(ws) {
      this._ws = ws || null;
    }

    setPolicy(policy) {
      const previousUsePCM = this._usePCM;
      if (policy && typeof policy === "object") {
        this._policy = policy;
      } else {
        this._policy = {};
      }
      this._usePCM = this._resolveUsePCM();
      if (this._usePCM && this._rec && !previousUsePCM) {
        const recorder = this._rec;
        try {
          if (recorder.state !== "inactive") {
            recorder.stop();
          }
        } catch (err) {
          console.warn("AudioRecorder policy switch stop failed", err);
        }
        this._rec = null;
        this._headerSent = false;
        this._resetStreamingTelemetry();
      }
    }

    get policy() {
      return this._policy || {};
    }

    _resolveUsePCM() {
      const policy = this._policy || {};
      try {
        const media = policy.media && typeof policy.media === "object" ? policy.media : null;
        if (media && typeof media.asr_input === "string" && media.asr_input === "pcm_16k") {
          return true;
        }
        const capture = policy.capture && typeof policy.capture === "object" ? policy.capture : null;
        if (capture && typeof capture.asr_input === "string" && capture.asr_input === "pcm_16k") {
          return true;
        }
        const audio = policy.audio && typeof policy.audio === "object" ? policy.audio : null;
        const pipeline = audio && typeof audio.pipeline === "object" ? audio.pipeline : null;
        if (pipeline && typeof pipeline.mode === "string" && pipeline.mode === "pcm16") {
          return true;
        }
      } catch {}
      return false;
    }

    _recorderPolicy() {
      const policy = this.policy;
      if (!policy || typeof policy !== "object") {
        return {};
      }
      const nested = policy.policy;
      if (!nested || typeof nested !== "object") {
        return {};
      }
      const recorder = nested.recorder;
      return recorder && typeof recorder === "object" ? recorder : {};
    }

    _asrPolicy() {
      const policy = this.policy;
      if (!policy || typeof policy !== "object") {
        return {};
      }
      const nested = policy.policy;
      if (!nested || typeof nested !== "object") {
        return {};
      }
      const asr = nested.asr;
      return asr && typeof asr === "object" ? asr : {};
    }

    _vadPolicy() {
      const directAsr = this.policy?.asr && typeof this.policy.asr === "object" ? this.policy.asr : null;
      if (directAsr && directAsr.vad && typeof directAsr.vad === "object") {
        return directAsr.vad;
      }
      const nested = this._asrPolicy();
      if (nested && nested.vad && typeof nested.vad === "object") {
        return nested.vad;
      }
      return {};
    }

    _resolveVadThreshold() {
      const vad = this._vadPolicy();
      const candidate = vad && Number.isFinite(Number(vad.rms_threshold)) ? Number(vad.rms_threshold) : null;
      if (candidate !== null && !Number.isNaN(candidate) && candidate >= 0) {
        return candidate;
      }
      return 0;
    }

    _shouldCommitOnVad() {
      const asr = this._asrPolicy();
      if (typeof asr.commit_on_vad_silence === "boolean") {
        return asr.commit_on_vad_silence;
      }
      const direct = this.policy?.asr && typeof this.policy.asr === "object" ? this.policy.asr : null;
      if (direct && typeof direct.commit_on_vad_silence === "boolean") {
        return direct.commit_on_vad_silence;
      }
      return true;
    }

    _commitSilenceMs() {
      const asr = this._asrPolicy();
      let value = asr && Number.isFinite(Number(asr.commit_silence_ms)) ? Number(asr.commit_silence_ms) : null;
      if (value === null) {
        const direct = this.policy?.asr && typeof this.policy.asr === "object" ? this.policy.asr : null;
        if (direct && Number.isFinite(Number(direct.commit_silence_ms))) {
          value = Number(direct.commit_silence_ms);
        }
      }
      if (value === null || Number.isNaN(value) || value < 0) {
        return 900;
      }
      return Math.round(value);
    }

    async _ensureAudioContext() {
      if (this._audioContext && this._audioContext.state !== "closed") {
        return this._audioContext;
      }
      const Ctor = typeof window !== "undefined" ? (window.AudioContext || window.webkitAudioContext) : null;
      if (!Ctor) {
        throw new Error("audio_context_unavailable");
      }
      try {
        this._audioContext = new Ctor();
        this._pcmWorkletLoaded = false;
      } catch (err) {
        console.warn("AudioContext create failed", err);
        throw err;
      }
      return this._audioContext;
    }

    _resetPcmState() {
      this._pcmResampleBuffer = [];
      this._pcmOutputBuffer = [];
      this._pcmResampleCursor = 0;
      this._pcmFrameSeq = 0;
      this._pcmLastTelemetryAt = 0;
      this._pcmUtteranceStartAt = null;
      this._pcmLastVoiceAt = null;
      this._pcmSilenceStartAt = null;
      this._pcmCommitSent = false;
    }

    _resetStreamingTelemetry() {
      const globalWindow = typeof window !== "undefined" ? window : null;
      if (!globalWindow) {
        return;
      }
      try {
        if (typeof globalWindow.__micChunks === "number") {
          globalWindow.__micChunks = 0;
        }
        if (typeof globalWindow.__micBytes === "number") {
          globalWindow.__micBytes = 0;
        }
      } catch {}
    }

    async _loadPcmWorklet(context) {
      if (this._pcmWorkletLoaded) {
        return true;
      }
      if (!context?.audioWorklet || typeof context.audioWorklet.addModule !== "function") {
        return false;
      }
      if (!AudioRecorder._pcmWorkletUrl) {
        const workletSource = `class PCM16CaptureProcessor extends AudioWorkletProcessor {\n  constructor() {\n    super();\n    this._seq = 0;\n  }\n  process(inputs) {\n    const input = inputs[0];\n    if (!input || input.length === 0) {\n      return true;\n    }\n    const channels = input.length;\n    const frames = input[0].length;\n    const chunk = new Float32Array(frames);\n    for (let i = 0; i < frames; i += 1) {\n      let sample = 0;\n      for (let ch = 0; ch < channels; ch += 1) {\n        sample += input[ch]?.[i] ?? 0;\n      }\n      chunk[i] = channels > 0 ? sample / channels : 0;\n    }\n    this.port.postMessage({ type: 'chunk', sampleRate: sampleRate, seq: this._seq++, data: chunk.buffer }, [chunk.buffer]);\n    return true;\n  }\n}\nregisterProcessor('pcm16-capture-processor', PCM16CaptureProcessor);`;
        const blob = new Blob([workletSource], { type: "application/javascript" });
        AudioRecorder._pcmWorkletUrl = URL.createObjectURL(blob);
      }
      try {
        await context.audioWorklet.addModule(AudioRecorder._pcmWorkletUrl);
        this._pcmWorkletLoaded = true;
        return true;
      } catch (err) {
        console.warn("AudioWorklet addModule failed", err);
        return false;
      }
    }

    async _ensurePcmGraph() {
      if (!this._usePCM) {
        return;
      }
      const context = await this._ensureAudioContext();
      if (!this._stream) {
        throw new Error("pcm_stream_unavailable");
      }
      this._resetPcmState();
      if (context.state === "suspended") {
        try {
          await context.resume();
        } catch {}
      }
      if (this._pcmSource && (this._pcmNode || this._pcmGainNode)) {
        return;
      }
      const source = context.createMediaStreamSource(this._stream);
      this._pcmSource = source;
      let attached = false;
      if (await this._loadPcmWorklet(context)) {
        try {
          const node = new AudioWorkletNode(context, "pcm16-capture-processor", { numberOfOutputs: 0 });
          node.port.onmessage = (event) => {
            const data = event?.data;
            if (!data || data.type !== "chunk") {
              return;
            }
            const buffer = data.data;
            if (!(buffer instanceof ArrayBuffer)) {
              return;
            }
            this._handlePcmChunk(new Float32Array(buffer), Number(data.sampleRate) || context.sampleRate);
          };
          source.connect(node);
          this._pcmNode = node;
          attached = true;
        } catch (err) {
          console.warn("AudioWorkletNode create failed", err);
          this._pcmNode = null;
        }
      }
      if (!attached) {
        const script = context.createScriptProcessor(4096, Math.max(1, source.channelCount || 1), 1);
        script.onaudioprocess = (event) => {
          const input = event?.inputBuffer;
          if (!input) {
            return;
          }
          const frames = input.length;
          const channels = input.numberOfChannels || 1;
          const chunk = new Float32Array(frames);
          for (let i = 0; i < frames; i += 1) {
            let sample = 0;
            for (let ch = 0; ch < channels; ch += 1) {
              sample += input.getChannelData(ch)?.[i] ?? 0;
            }
            chunk[i] = channels > 0 ? sample / channels : 0;
          }
          this._handlePcmChunk(chunk, context.sampleRate);
        };
        const gain = context.createGain();
        gain.gain.value = 0;
        source.connect(script);
        script.connect(gain);
        gain.connect(context.destination);
        this._pcmNode = script;
        this._pcmGainNode = gain;
      }
    }

    _teardownPcmGraph() {
      if (this._pcmNode) {
        try {
          this._pcmNode.port?.close?.();
        } catch {}
        try {
          this._pcmNode.disconnect?.();
        } catch {}
      }
      if (this._pcmGainNode) {
        try {
          this._pcmGainNode.disconnect?.();
        } catch {}
      }
      if (this._pcmSource) {
        try {
          this._pcmSource.disconnect?.();
        } catch {}
      }
      this._pcmSource = null;
      this._pcmNode = null;
      this._pcmGainNode = null;
      this._resetPcmState();
    }

    _handlePcmChunk(chunk, inputSampleRate) {
      if (!Array.isArray(this._pcmResampleBuffer)) {
        this._pcmResampleBuffer = [];
      }
      if (!Array.isArray(this._pcmOutputBuffer)) {
        this._pcmOutputBuffer = [];
      }
      if (!(chunk instanceof Float32Array) || chunk.length === 0) {
        return;
      }
      const ratio = Number(inputSampleRate) / PCM_SAMPLE_RATE;
      if (!Number.isFinite(ratio) || ratio <= 0) {
        return;
      }
      for (let i = 0; i < chunk.length; i += 1) {
        const value = Number.isFinite(chunk[i]) ? chunk[i] : 0;
        this._pcmResampleBuffer.push(value);
      }
      const buffer = this._pcmResampleBuffer;
      let cursor = Number(this._pcmResampleCursor) || 0;
      const output = this._pcmOutputBuffer;
      while (cursor + 1 < buffer.length) {
        const index = Math.floor(cursor);
        const frac = cursor - index;
        const sample0 = buffer[index];
        const sample1 = buffer[index + 1] ?? sample0;
        const interpolated = sample0 + (sample1 - sample0) * frac;
        output.push(interpolated);
        if (output.length >= PCM_FRAME_SAMPLES) {
          const frame = output.splice(0, PCM_FRAME_SAMPLES);
          this._emitPcmFrame(frame);
        }
        cursor += ratio;
      }
      const consumed = Math.max(0, Math.floor(cursor) - 1);
      if (consumed > 0) {
        buffer.splice(0, consumed);
        cursor -= consumed;
      }
      this._pcmResampleCursor = cursor;
      this._pcmOutputBuffer = output;
    }

    _logMicFrame(rms, bytes) {
      const seq = this._pcmFrameSeq;
      const normalizedRms = Number.isFinite(rms) ? rms : 0;
      const normalizedBytes = Number.isFinite(bytes) ? bytes : 0;
      const message = `frame seq=${seq} rms=${normalizedRms.toFixed(1)} bytes=${normalizedBytes}`;
      try {
        const hub = typeof window !== "undefined" ? window.AppState?.hub : null;
        if (hub && typeof hub.log === "function") {
          hub.log("client.mic", message);
          return;
        }
      } catch {}
      try {
        console.log(`client.mic ${message}`);
      } catch {}
    }

    _sendCommitControl(detail) {
      const payload = {
        type: "client.telemetry",
        event: "EVT_AUDIO_CHUNK_SENT_CLIENT",
        meta: {
          commit: true,
          reason: detail.reason,
          dur_ms: detail.dur_ms,
          silence_ms: detail.silence_ms,
        },
      };
      try {
        const client = typeof window !== "undefined" ? window.WSClient : null;
        if (client && typeof client.send === "function") {
          client.send(payload);
        }
      } catch (err) {
        console.warn("AudioRecorder commit control send failed", err);
      }
    }

    _emitVadCommit(durMs, silenceMs) {
      const detail = {
        evt: "commit",
        reason: "vad_silence",
        dur_ms: Math.max(0, Math.round(durMs)),
        silence_ms: Math.max(0, Math.round(silenceMs)),
      };
      try {
        const hub = typeof window !== "undefined" ? window.AppState?.hub : null;
        if (hub && typeof hub.log === "function") {
          hub.log("client.asr", detail);
        }
      } catch {}
      try {
        console.log(`client.asr evt=commit reason=vad_silence dur_ms=${detail.dur_ms} silence_ms=${detail.silence_ms}`);
      } catch {}
      this._sendCommitControl(detail);
    }

    _emitPcmFrame(frame) {
      if (!Array.isArray(frame) || frame.length === 0) {
        return;
      }
      const samples = frame.length;
      const pcm = new Int16Array(samples);
      let sumSquares = 0;
      for (let i = 0; i < samples; i += 1) {
        const value = Number.isFinite(frame[i]) ? frame[i] : 0;
        const clipped = Math.max(-1, Math.min(1, value));
        const intSample = Math.round(clipped * 32767);
        pcm[i] = intSample;
        sumSquares += intSample * intSample;
      }
      const rms = Math.sqrt(sumSquares / samples);
      const threshold = this._resolveVadThreshold();
      const silent = rms <= threshold;
      const now = Date.now();

      if (!silent) {
        this._pcmLastVoiceAt = now;
        if (!this._pcmUtteranceStartAt) {
          this._pcmUtteranceStartAt = now;
          this._pcmCommitSent = false;
        }
        this._pcmSilenceStartAt = null;
      } else if (this._pcmUtteranceStartAt && !this._pcmCommitSent) {
        if (this._pcmSilenceStartAt === null) {
          this._pcmSilenceStartAt = now;
        }
        if (this._shouldCommitOnVad()) {
          const silenceMs = now - this._pcmSilenceStartAt;
          if (silenceMs >= this._commitSilenceMs()) {
            const utteranceDur = now - this._pcmUtteranceStartAt;
            this._emitVadCommit(utteranceDur, silenceMs);
            this._pcmCommitSent = true;
            this._pcmUtteranceStartAt = null;
            this._pcmSilenceStartAt = null;
            this._pcmLastVoiceAt = null;
          }
        }
      }

      if (silent || !this._sendGate || this._sendMuted) {
        return;
      }

      if (!this._headerSent) {
        this._sendAudioHeader();
        if (!this._headerSent) {
          return;
        }
      }

      const socket = this._ws;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
      }

      const packet = pcm.buffer;
      try {
        socket.send(packet);
      } catch (err) {
        const globalWindow = typeof window !== "undefined" ? window : null;
        const micOutcome = globalWindow?.__MIC_OUTCOME;
        globalWindow?.__logMic?.({ outcome: (micOutcome && micOutcome.ERROR_WS_SEND) || 'error_ws_send', message: err?.message });
        globalWindow?.__logStage?.('client.audio', { outcome: 'error', message: err?.message });
        console.warn("diag=audio_chunk_send_failed %o", err);
        return;
      }

      const globalWindow = typeof window !== "undefined" ? window : null;
      if (globalWindow && typeof globalWindow.__micChunks === "number") {
        if (globalWindow.__micChunks === 0) {
          globalWindow.__micChunks = 1;
          globalWindow.__micBytes += packet.byteLength;
          const armedAt = globalWindow.__micArmedAt || 0;
          const firstChunkMs = Math.max(0, Date.now() - armedAt);
          const logMic = globalWindow.__logMic;
          const logStage = globalWindow.__logStage;
          const micOutcome = globalWindow.__MIC_OUTCOME;
          logMic?.({ outcome: (micOutcome && micOutcome.STREAMING) || 'streaming', first_chunk_ms: firstChunkMs });
          logStage?.('client.audio', { outcome: 'packet_sent', packet_bytes: packet.byteLength, send_q_len: socket.bufferedAmount });
          if (armedAt && globalWindow.__micChunks === 1) {
            logStage?.('client.perf', { outcome: 'mark', name: 'first_chunk_ms', t_ms: firstChunkMs });
          }
        } else {
          globalWindow.__micChunks += 1;
          globalWindow.__micBytes += packet.byteLength;
          if ((globalWindow.__micChunks % 50) === 0) {
            const logMic = globalWindow.__logMic;
            const micOutcome = globalWindow.__MIC_OUTCOME;
            logMic?.({ outcome: (micOutcome && micOutcome.STREAMING_HEARTBEAT) || 'streaming_heartbeat' });
          }
        }
      }

      try {
        console.info("diag=audio_chunk_sent bytes=%d seq=%d rms=%d", packet.byteLength, this._pcmFrameSeq, Math.round(rms));
      } catch {}

      this._pcmFrameSeq += 1;
      if (!this._pcmLastTelemetryAt || (now - this._pcmLastTelemetryAt) >= PCM_TELEMETRY_INTERVAL_MS) {
        this._pcmLastTelemetryAt = now;
        this._logMicFrame(rms, packet.byteLength);
      }
    }

    _shouldStopOnTtsStart() {
      const recorder = this._recorderPolicy();
      if (typeof recorder.stop_on_tts_start === "boolean") {
        return recorder.stop_on_tts_start;
      }
      return false;
    }

    _shouldMuteDuringTts() {
      const recorder = this._recorderPolicy();
      if (typeof recorder.mute_send_during_tts === "boolean") {
        return recorder.mute_send_during_tts;
      }
      return true;
    }

    _setSendMuted(muted, reason) {
      const next = Boolean(muted);
      if (this._sendMuted === next) {
        return;
      }
      this._sendMuted = next;
      const label = next ? "diag=send_gate_muted" : "diag=send_gate_unmuted";
      const detail = typeof reason === "string" && reason ? reason : "policy";
      try {
        console.info(`${label} reason=%s`, detail);
      } catch (err) {
        console.info(label);
      }
    }

    _sendAudioHeader() {
      if (this._headerSent) {
        return true;
      }
      const socket = this._ws;
      if (!socket) {
        return false;
      }
      if (socket.readyState === WebSocket.CONNECTING) {
        const handleOpen = () => {
          try {
            socket.removeEventListener("open", handleOpen);
          } catch {}
          this._sendAudioHeader();
        };
        try {
          socket.addEventListener("open", handleOpen, { once: true });
        } catch {
          socket.addEventListener("open", handleOpen);
        }
        return false;
      }
      if (socket.readyState !== WebSocket.OPEN) {
        return false;
      }
      let payload;
      if (this._usePCM) {
        payload = {
          type: "audio.header",
          format: "pcm",
          sample_rate: PCM_SAMPLE_RATE,
          channels: 1,
        };
      } else {
        const mediaPolicy = this.policy?.media && typeof this.policy.media === "object" ? this.policy.media : {};
        const sampleRate = Number.isFinite(mediaPolicy?.sample_rate) ? mediaPolicy.sample_rate : 48000;
        const channels = Number.isFinite(mediaPolicy?.channels) ? mediaPolicy.channels : 1;
        payload = {
          type: "audio.header",
          format: "opus",
          sample_rate: sampleRate,
          channels,
        };
      }
      try {
        socket.send(JSON.stringify(payload));
        this._headerSent = true;
        console.info(
          "diag=audio_header_sent sr=%d channels=%d format=%s",
          payload.sample_rate,
          payload.channels,
          payload.format
        );
        return true;
      } catch (err) {
        console.warn("diag=audio_header_send_failed %o", err);
        return false;
      }
    }

    async _ensureArmed() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        logMic?.({ outcome: (micOutcome && micOutcome.ERROR_NO_DEVICE) || 'error_no_device', message: 'media_devices_unavailable' });
        throw new Error("media_devices_unavailable");
      }
      if (!this._stream) {
        try {
          const constraints = this._usePCM
            ? {
                audio: {
                  echoCancellation: true,
                  noiseSuppression: true,
                  autoGainControl: true,
                },
              }
            : { audio: true };
          this._stream = await navigator.mediaDevices.getUserMedia(constraints);
          const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
          const logMic = typeof window !== "undefined" ? window.__logMic : null;
          const summary = currentInputDeviceSummary(this._stream);
          logMic?.({ outcome: (micOutcome && micOutcome.PERM_GRANTED) || 'perm_granted', perm: 'granted', device: summary });
          console.info("diag=mic_armed");
        } catch (err) {
          const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
          const logMic = typeof window !== "undefined" ? window.__logMic : null;
          const denied = err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
          logMic?.({
            outcome: denied ? (micOutcome && micOutcome.ERROR_DENIED) || 'error_denied' : (micOutcome && micOutcome.ERROR_GUM) || 'error_getuser_media',
            perm: denied ? 'denied' : 'error',
            message: err?.message,
          });
          throw err;
        }
        if (!this._wakeInit) {
          try {
            WakeWord.init(this._stream);
          } catch {}
          this._wakeInit = true;
        }
      }
      if (this._usePCM) {
        await this._ensurePcmGraph();
        return;
      }
      if (!window.MediaRecorder) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        const logStage = typeof window !== "undefined" ? window.__logStage : null;
        logMic?.({ outcome: (micOutcome && micOutcome.ERROR_GUM) || 'error_getuser_media', message: 'media_recorder_unavailable' });
        logStage?.('client.audio', { outcome: 'error', message: 'media_recorder_unavailable' });
        throw new Error("media_recorder_unavailable");
      }
      if (typeof MediaRecorder.isTypeSupported === "function" && !MediaRecorder.isTypeSupported(OPUS_MIME)) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        const logStage = typeof window !== "undefined" ? window.__logStage : null;
        logMic?.({ outcome: (micOutcome && micOutcome.ERROR_GUM) || 'error_getuser_media', message: 'media_recorder_unsupported' });
        logStage?.('client.audio', { outcome: 'error', message: 'media_recorder_unsupported' });
        throw new Error("media_recorder_unsupported");
      }
      if (!this._rec) {
        this._rec = new MediaRecorder(this._stream, { mimeType: OPUS_MIME });
        const logStage = typeof window !== "undefined" ? window.__logStage : null;
        logStage?.('client.audio', { outcome: 'encoder_ready', format: 'webm_opus', sr: 48000, channels: 1 });
        this._rec.addEventListener("dataavailable", async (event) => {
          if (this._usePCM) {
            return;
          }
          if (!event?.data || event.data.size === 0) {
            return;
          }
          const buf = await event.data.arrayBuffer();
          if (!buf || buf.byteLength === 0) {
            return;
          }
          if (!this._sendGate || this._sendMuted) {
            return;
          }
          const socket = this._ws;
          if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
          }
          const packet = buf;
          const globalWindow = typeof window !== "undefined" ? window : null;
          const logMic = globalWindow?.__logMic;
          const logStage = globalWindow?.__logStage;
          const micOutcome = globalWindow?.__MIC_OUTCOME;
          if (globalWindow && typeof globalWindow.__micChunks === "number") {
            if (globalWindow.__micChunks === 0) {
              globalWindow.__micChunks = 1;
              globalWindow.__micBytes += (packet?.byteLength ?? 0);
              const armedAt = globalWindow.__micArmedAt || 0;
              const firstChunkMs = Math.max(0, Date.now() - armedAt);
              logMic?.({ outcome: (micOutcome && micOutcome.STREAMING) || 'streaming', first_chunk_ms: firstChunkMs });
              logStage?.('client.audio', { outcome: 'packet_sent', packet_bytes: packet?.byteLength ?? 0, send_q_len: socket.bufferedAmount });
              if (armedAt && globalWindow.__micChunks === 1) {
                logStage?.('client.perf', { outcome: 'mark', name: 'first_chunk_ms', t_ms: firstChunkMs });
              }
            } else {
              globalWindow.__micChunks += 1;
              globalWindow.__micBytes += (packet?.byteLength ?? 0);
              if ((globalWindow.__micChunks % 50) === 0) {
                logMic?.({ outcome: (micOutcome && micOutcome.STREAMING_HEARTBEAT) || 'streaming_heartbeat' });
              }
            }
          }
          try {
            socket.send(packet);
            console.info("diag=audio_chunk_sent bytes=%d", packet.byteLength);
          } catch (err) {
            logMic?.({ outcome: (micOutcome && micOutcome.ERROR_WS_SEND) || 'error_ws_send', message: err?.message });
            logStage?.('client.audio', { outcome: 'error', message: err?.message });
            console.warn("diag=audio_chunk_send_failed %o", err);
          }
        });
        this._rec.addEventListener("stop", () => {
          this._rec = null;
          const logMic = typeof window !== "undefined" ? window.__logMic : null;
          const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
          logMic?.({ outcome: (micOutcome && micOutcome.STOPPED) || 'stopped', reason: 'recorder_stop' });
        });
        this._rec.addEventListener("error", (event) => {
          console.warn("diag=media_recorder_error %o", event);
          const logMic = typeof window !== "undefined" ? window.__logMic : null;
          const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
          const logStage = typeof window !== "undefined" ? window.__logStage : null;
          const message = event?.error?.message || event?.name || "recorder_error";
          logMic?.({ outcome: (micOutcome && micOutcome.ERROR_UNKNOWN) || 'error_unknown', message });
          logStage?.('client.audio', { outcome: 'error', message });
        });
      }
      if (this._rec.state !== "recording") {
        this._rec.start(SEND_TIMESLICE_MS);
        console.info("diag=media_recorder_start timeslice_ms=%d", SEND_TIMESLICE_MS);
      }
    }

    _updateRecorderState(active, reason) {
      const nextActive = Boolean(active);
      this._active = nextActive;
      const payload = { active: nextActive };
      if (reason && typeof reason === "string") {
        payload.reason = reason;
      }
      try {
        if (this._state) {
          if (typeof this._state.setState === "function") {
            this._state.setState({ recorder: { active: payload.active } });
          }
          this._state.recorder = { active: payload.active };
          this._state.emit?.(payload.active ? "recordingStarted" : "recordingStopped", payload);
        }
      } catch (err) {
        console.warn("AudioRecorder state update failed", err);
      }
      emitEvent(CLIENT_HUD_STATE_EVENT, {
        type: CLIENT_HUD_STATE_EVENT,
        meta: { state: this._active ? "Listening" : "Idle", source: "client" }
      });
      if (!nextActive) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        logMic?.({ outcome: (micOutcome && micOutcome.STOPPED) || 'stopped', reason, source: 'recorder_state' });
      }
      if (!nextActive && this._micOpenEmitted) {
        this._micOpenEmitted = false;
      }
      if (nextActive && !this._micOpenEmitted) {
        this._micOpenEmitted = true;
        const vendorLabel = this._usePCM ? "pcm16" : "webm_opus";
        emitEvent(CLIENT_MIC_OPEN_EVENT, {
          type: CLIENT_MIC_OPEN_EVENT,
          ts: Date.now(),
          vendor: vendorLabel
        });
      }
    }

    async start(policy = {}) {
      this.setPolicy(policy);
      await this._ensureArmed();
      return this._stream;
    }

    async startMicCaptureIfIdle(policy = {}) {
      this.setPolicy(policy);
      await this._ensureArmed();
      return true;
    }

    async startListening(policy = {}) {
      this.setPolicy(policy);
      await this._ensureArmed();
      this._sendAudioHeader();
      if (!this._sendGate) {
        this._sendGate = true;
        const reason = typeof policy?.reason === "string" && policy.reason ? policy.reason : "start_listening";
        console.info("diag=send_gate_open reason=%s", reason);
        this._updateRecorderState(true, reason);
      }
      this._setSendMuted(false, "start_listening");
      return true;
    }

    stopListening(opts = {}) {
      const reason = typeof opts?.reason === "string" && opts.reason ? opts.reason : "stop_listening";
      if (this._sendGate) {
        console.info("diag=send_gate_closed reason=%s", reason);
      }
      this._sendGate = false;
      this._updateRecorderState(false, reason);
      this._micOpenEmitted = false;
      this._setSendMuted(false, reason);
      this._headerSent = false;
      if (this._usePCM) {
        this._resetPcmState();
      }
    }

    handleStopListening(opts = {}) {
      this.stopListening(opts);
    }

    handleTtsStart() {
      if (this._shouldStopOnTtsStart()) {
        this.stopListening({ reason: "tts_active" });
        return;
      }
      if (this._shouldMuteDuringTts()) {
        this._setSendMuted(true, "tts_active");
      }
    }

    handleTtsEnd() {
      this._setSendMuted(false, "tts_end");
    }

    handleWsClose() {
      this._headerSent = false;
      this.endSession();
    }

    stop() {
      this.endSession();
    }

    endSession() {
      this._sendGate = false;
      this._setSendMuted(false, "session_end");
      this._updateRecorderState(false, "session_end");
      this._headerSent = false;
      if (this._pcmNode || this._pcmSource || this._pcmGainNode) {
        try {
          this._teardownPcmGraph();
        } catch (err) {
          console.warn("AudioRecorder pcm teardown failed", err);
        }
      }
      try {
        if (this._rec && this._rec.state !== "inactive") {
          this._rec.stop();
        }
      } catch (err) {
        console.warn("AudioRecorder stop error", err);
      }
      this._rec = null;
      this._micOpenEmitted = false;
      this._active = false;
      this._wakeInit = false;
      if (this._audioContext) {
        try {
          if (this._audioContext.state !== "closed") {
            this._audioContext.close();
          }
        } catch {}
        this._audioContext = null;
      }
      if (this._stream) {
        try {
          this._stream.getTracks?.().forEach((track) => track.stop?.());
        } catch {}
        this._stream = null;
      }
    }
  }

  if (typeof AudioRecorder._pcmWorkletUrl === "undefined") {
    AudioRecorder._pcmWorkletUrl = null;
  }

  const initialWs = typeof window !== "undefined" ? window.ws || null : null;
  const recorder = new AudioRecorder(initialWs, window.AppState || null);
  if (typeof window !== "undefined") {
    window.addEventListener("ws.close", () => {
      try {
        recorder.handleWsClose();
      } catch (err) {
        console.warn("AudioRecorder ws.close handler failed", err);
      }
    });
  }
  window.AudioRecorder = recorder;
})();
