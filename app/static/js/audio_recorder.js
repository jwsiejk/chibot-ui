import { WakeWord } from "./wake_word.js";

/** POLICY: PCM-only recorder; no PTT; VAD-driven barge-in. */
(() => {
  // ===== Constants (best practice) =====
  const PCM_SAMPLE_RATE = 16000;                 // 16 kHz
  const FRAME_MS = 50;                            // 50 ms
  const PCM_FRAME_SAMPLES = Math.round(PCM_SAMPLE_RATE * (FRAME_MS / 1000)); // 800 samples
  const PCM_TELEMETRY_INTERVAL_MS = 1000;

  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";

  // ===== Small helpers =====
  function emitEvent(type, detail) {
    try { window.dispatchEvent(new CustomEvent(type, { detail })); } catch {}
  }
  function getAppState() {
    try { return window.AppState || null; } catch { return null; }
  }
  function getGateSnapshot() {
    const appState = getAppState();
    let snapshot = null;
    try { snapshot = typeof appState?.getState === "function" ? appState.getState() : null; } catch {}
    const asrValue = typeof snapshot?.asrReady === "boolean" ? snapshot.asrReady : !!appState?.asrReady;
    const ttsValue = typeof snapshot?.ttsActive === "boolean" ? snapshot.ttsActive : !!appState?.ttsActive;
    let micPermValue = null;
    if (typeof snapshot?.micPermissionGranted === "boolean") micPermValue = snapshot.micPermissionGranted;
    else if (typeof appState?.micPermissionGranted === "boolean") micPermValue = appState.micPermissionGranted;
    return { asrReady: !!asrValue, micPerm: !!micPermValue, ttsActive: !!ttsValue };
  }
  function logMicBreadcrumb(detail = {}) {
    const appState = getAppState();
    try {
      if (appState?.hub?.log) { appState.hub.log("client.mic", detail); return true; }
    } catch {}
    try { window.dispatchEvent(new CustomEvent("client.log", { detail: { label: "client.mic", detail } })); return true; } catch {}
    try { console.log("client.mic", detail); } catch {}
    return false;
  }
  function logMicEventString(text) {
    if (!text) return false;
    const appState = getAppState();
    try { appState?.hub?.log?.("client.mic", text); } catch {}
    try { window.dispatchEvent(new CustomEvent("client.log", { detail: { label: "client.mic", detail: text } })); return true; } catch {}
    try { console.log(`client.mic ${text}`); return true; } catch {}
    return false;
  }
  function currentInputDeviceSummary(stream) {
    if (!stream) return null;
    try {
      const track = stream.getAudioTracks?.()[0];
      if (!track) return null;
      const summary = {};
      const settings = track.getSettings?.() || {};
      if (settings.deviceId) summary.id = settings.deviceId;
      if (track.label) summary.label = track.label;
      if (Number.isFinite(settings.sampleRate)) summary.sample_rate = settings.sampleRate;
      if (Number.isFinite(settings.channelCount)) summary.channels = settings.channelCount;
      return Object.keys(summary).length ? summary : null;
    } catch { return null; }
  }

  // Cooldown utilities (re-arm lock)
  function getRearmCooldownRemainingMs() {
    const appState = getAppState(); if (!appState) return 0;
    const untilValue = Number(appState.__no_rearm_until); if (!Number.isFinite(untilValue)) return 0;
    const delta = Math.ceil(untilValue - Date.now()); return delta > 0 ? delta : 0;
  }
  function logRearmBlocked(trigger, msRemaining, reason = "cooldown") {
    if (!(msRemaining > 0)) return;
    logMicBreadcrumb({ event: "rearm_blocked", reason, ms_remaining: msRemaining, trigger });
    logMicEventString(`evt=rearm_blocked reason=${reason}`);
  }
  const waitMs = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitForRearmCooldown(trigger) {
    let remaining = getRearmCooldownRemainingMs();
    while (remaining > 0) { logRearmBlocked(trigger, remaining); await waitMs(remaining); remaining = getRearmCooldownRemainingMs(); }
  }

  // ===== Recorder =====
  class AudioRecorder {
    constructor(ws, appState) {
      this._ws = ws || null;
      this._state = appState || null;
      this._stream = null;
      this._sendGate = false;
      this._policy = {};
      this._micOpenEmitted = false;
      this._active = false;
      this._sendMuted = false;

      this._headerSent = false;
      this._audioHeaderSignature = null;

      this._audioContext = null;
      this._pcmSource = null;
      this._pcmNode = null;
      this._pcmGainNode = null;

      // resample + output
      this._pcmResampleBuffer = [];
      this._pcmOutputBuffer = [];
      this._pcmResampleCursor = 0;
      this._pcmFrameSeq = 0;
      this._pcmLastTelemetryAt = 0;

      // VAD helpers
      this._pcmUtteranceStartAt = null;
      this._pcmLastVoiceAt = null;
      this._pcmSilenceStartAt = null;
      this._pcmCommitSent = false;

      // async guards
      this._pcmWorkletLoaded = false;
      this._armingPromise = null;

      // last stop record
      this._lastStopLog = null;
    }

    setSocket(ws) { this._ws = ws || null; }
    get policy() { return this._policy || {}; }

    setPolicy(policy) {
      const hasPayload = policy && typeof policy === "object" && Object.keys(policy).length > 0;
      const next = hasPayload ? policy : (this._policy && typeof this._policy === "object" ? this._policy : {});
      if (hasPayload) {
        const nextSig = this._deriveAudioHeaderSignature(next);
        if (this._audioHeaderSignature !== nextSig) this._headerSent = false;
        this._audioHeaderSignature = nextSig;
        this._resetStreamingTelemetry();
      }
      this._policy = next;
      if (hasPayload) {
        const media = next.media || {};
        const audio = next.audio?.pipeline?.mode || null;
        try { console.info("diag=recorder_policy format=pcm asr_input=%s pipeline=%s", media?.asr_input ?? null, audio ?? null); } catch {}
      }
    }

    _deriveAudioHeaderSignature(policy) {
      try {
        const media = policy?.media || {};
        const nestedMedia = policy?.policy?.media || {};
        const audio = policy?.audio?.pipeline || {};
        const nestedAudio = policy?.policy?.audio?.pipeline || {};
        const sig = {
          input: media.asr_input ?? nestedMedia.asr_input ?? null,
          rate: Number.isFinite(media.asr_rate_hz) ? Number(media.asr_rate_hz)
                : (Number.isFinite(nestedMedia.asr_rate_hz) ? Number(nestedMedia.asr_rate_hz) : null),
          channels: Number.isFinite(media.asr_channels) ? Number(media.asr_channels)
                : (Number.isFinite(nestedMedia.asr_channels) ? Number(nestedMedia.asr_channels) : null),
          mode: typeof audio.mode === "string" ? audio.mode : (typeof nestedAudio.mode === "string" ? nestedAudio.mode : null),
        };
        return JSON.stringify(sig);
      } catch { return null; }
    }

    _recorderPolicy() {
      const nested = this.policy?.policy;
      return (nested && typeof nested.recorder === "object") ? nested.recorder : {};
    }
    _asrPolicy() {
      const nested = this.policy?.policy;
      return (nested && typeof nested.asr === "object") ? nested.asr : {};
    }
    _vadPolicy() {
      const direct = this.policy?.asr;
      if (direct?.vad && typeof direct.vad === "object") return direct.vad;
      const nested = this._asrPolicy();
      return (nested?.vad && typeof nested.vad === "object") ? nested.vad : {};
    }
    _resolveVadThreshold() {
      const vad = this._vadPolicy();
      const v = Number(vad?.rms_threshold);
      return Number.isFinite(v) && v >= 0 ? v : 0;
    }
    _shouldCommitOnVad() {
      const asr = this._asrPolicy();
      if (typeof asr.commit_on_vad_silence === "boolean") return asr.commit_on_vad_silence;
      const direct = this.policy?.asr;
      if (direct && typeof direct.commit_on_vad_silence === "boolean") return direct.commit_on_vad_silence;
      return true;
    }
    _commitSilenceMs() {
      const asr = this._asrPolicy();
      const v = Number(asr?.commit_silence_ms ?? this.policy?.asr?.commit_silence_ms);
      if (!Number.isFinite(v) || v < 0) return 900;
      return Math.round(v);
    }

    async _ensureAudioContext() {
      if (this._audioContext && this._audioContext.state !== "closed") return this._audioContext;
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) throw new Error("audio_context_unavailable");
      this._audioContext = new Ctor();
      this._pcmWorkletLoaded = false;
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
    _captureTimesliceMs() {
      const p = this._policy || {};
      const cap = p.capture || p.policy?.capture || {};
      const raw = cap?.timeslice_ms;
      if (raw === undefined || raw === null || raw === "") return null;
      if (Number.isFinite(raw)) return Math.round(raw);
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) return Math.round(parsed);
      if (typeof raw === "string") return raw.trim() || null;
      return null;
    }
    _resetStreamingTelemetry() {
      try {
        if (typeof window.__micChunks === "number") window.__micChunks = 0;
        if (typeof window.__micBytes === "number") window.__micBytes = 0;
      } catch {}
    }

    async _loadPcmWorklet(context) {
      if (this._pcmWorkletLoaded) return true;
      if (!context?.audioWorklet?.addModule) return false;
      if (!AudioRecorder._pcmWorkletUrl) {
        const workletSource = `
class PCM16CaptureProcessor extends AudioWorkletProcessor {
  constructor(){ super(); this._seq = 0; }
  process(inputs){
    const chs = inputs[0];
    if (!chs || chs.length === 0) return true;
    const L = chs[0]?.length || 0;
    const out = new Float32Array(L);
    for (let i=0;i<L;i++){
      let s=0;
      for (let c=0;c<chs.length;c++){ s += chs[c]?.[i] ?? 0; }
      out[i] = chs.length ? s/chs.length : 0;
    }
    this.port.postMessage({type:'chunk', sampleRate: sampleRate, seq:this._seq++, data: out.buffer}, [out.buffer]);
    return true;
  }
}
registerProcessor('pcm16-capture-processor', PCM16CaptureProcessor);
        `.trim();
        AudioRecorder._pcmWorkletUrl = URL.createObjectURL(new Blob([workletSource], { type: "application/javascript" }));
      }
      try { await context.audioWorklet.addModule(AudioRecorder._pcmWorkletUrl); this._pcmWorkletLoaded = true; return true; }
      catch { return false; }
    }

    async _ensurePcmGraph() {
      const context = await this._ensureAudioContext();
      if (!this._stream) throw new Error("pcm_stream_unavailable");
      this._resetPcmState();
      if (context.state === "suspended") { try { await context.resume(); } catch {} }

      if (this._pcmSource && (this._pcmNode || this._pcmGainNode)) return;
      const source = context.createMediaStreamSource(this._stream);
      this._pcmSource = source;

      // Prefer Worklet; fallback to ScriptProcessor if not available
      let attached = false;
      if (await this._loadPcmWorklet(context)) {
        try {
          const node = new AudioWorkletNode(context, "pcm16-capture-processor", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
          node.port.onmessage = (evt) => {
            const msg = evt?.data; if (!msg || msg.type !== "chunk") return;
            const buf = msg.data; if (!(buf instanceof ArrayBuffer)) return;
            this._handlePcmChunk(new Float32Array(buf), Number(msg.sampleRate) || context.sampleRate);
          };
          source.connect(node);
          const gain = context.createGain(); gain.gain.value = 0; node.connect(gain); gain.connect(context.destination);
          this._pcmNode = node; this._pcmGainNode = gain; attached = true;
        } catch { try { source.disconnect(); } catch {} this._pcmNode = null; this._pcmGainNode = null; }
      }
      if (!attached) {
        const script = context.createScriptProcessor(4096, Math.max(1, source.channelCount || 1), 1);
        script.onaudioprocess = (e) => {
          const input = e?.inputBuffer; if (!input) return;
          const L = input.length, C = input.numberOfChannels || 1;
          const chunk = new Float32Array(L);
          for (let i=0;i<L;i++){ let s=0; for (let c=0;c<C;c++){ s += input.getChannelData(c)?.[i] ?? 0; } chunk[i] = C ? s/C : 0; }
          this._handlePcmChunk(chunk, context.sampleRate);
        };
        const gain = context.createGain(); gain.gain.value = 0;
        source.connect(script); script.connect(gain); gain.connect(context.destination);
        this._pcmNode = script; this._pcmGainNode = gain;
      }
    }

    _teardownPcmGraph() {
      try { this._pcmNode?.port?.close?.(); } catch {}
      try { this._pcmNode?.disconnect?.(); } catch {}
      try { this._pcmGainNode?.disconnect?.(); } catch {}
      try { this._pcmSource?.disconnect?.(); } catch {}
      this._pcmSource = this._pcmNode = this._pcmGainNode = null;
      this._resetPcmState();
    }

    // ===== Core: handle float32 input -> 16k -> Int16 LE bytes (50 ms frames) =====
    _handlePcmChunk(chunk, inputSampleRate) {
      if (!(chunk instanceof Float32Array) || chunk.length === 0) return;
      const ratio = Number(inputSampleRate) / PCM_SAMPLE_RATE;
      if (!Number.isFinite(ratio) || ratio <= 0) return;

      // Accumulate into resample buffer
      for (let i=0;i<chunk.length;i++){ const v = Number.isFinite(chunk[i]) ? chunk[i] : 0; this._pcmResampleBuffer.push(v); }
      const buf = this._pcmResampleBuffer; let cursor = Number(this._pcmResampleCursor) || 0;

      // Linear resample into output buffer; flush in exact 800-sample frames
      while (cursor + 1 < buf.length) {
        const i0 = Math.floor(cursor), frac = cursor - i0;
        const s0 = buf[i0], s1 = buf[i0+1] ?? s0;
        const s = s0 + (s1 - s0) * frac;
        this._pcmOutputBuffer.push(s);
        if (this._pcmOutputBuffer.length >= PCM_FRAME_SAMPLES) {
          const frame = this._pcmOutputBuffer.splice(0, PCM_FRAME_SAMPLES);
          this._emitPcmFrame(frame);
        }
        cursor += ratio;
      }
      const consumed = Math.max(0, Math.floor(cursor) - 1);
      if (consumed > 0) { buf.splice(0, consumed); cursor -= consumed; }
      this._pcmResampleCursor = cursor;
    }

    _emitPcmFrame(frame) {
      if (!Array.isArray(frame) || frame.length === 0) return;

      // VAD / RMS on int16 domain but we’ll pack bytes explicitly little-endian
      const samples = frame.length;
      const bytesPerSample = 2;
      const packetLen = samples * bytesPerSample;
      const ab = new ArrayBuffer(packetLen);
      const view = new DataView(ab);

      let sumSquares = 0;
      for (let i=0;i<samples;i++) {
        let f = Number.isFinite(frame[i]) ? frame[i] : 0;
        if (f > 1) f = 1; else if (f < -1) f = -1;
        let intSample = f < 0 ? Math.round(f * 32768) : Math.round(f * 32767);
        if (intSample > 32767) intSample = 32767;
        if (intSample < -32768) intSample = -32768;
        view.setInt16(i*2, intSample, true); // little-endian
        sumSquares += intSample * intSample;
      }

      const rms = Math.sqrt(sumSquares / samples);
      const threshold = this._resolveVadThreshold();
      const silent = rms <= threshold;
      const now = Date.now();

      // VAD bookkeeping + commit policy
      if (!silent) {
        this._pcmLastVoiceAt = now;
        if (!this._pcmUtteranceStartAt) {
          logMicBreadcrumb({ event: "vad_start", gates: getGateSnapshot() });
          this._pcmUtteranceStartAt = now;
          this._pcmCommitSent = false;
        }
        this._pcmSilenceStartAt = null;
      } else if (this._pcmUtteranceStartAt && !this._pcmCommitSent && this._shouldCommitOnVad()) {
        if (this._pcmSilenceStartAt === null) this._pcmSilenceStartAt = now;
        const silenceMs = now - this._pcmSilenceStartAt;
        if (silenceMs >= this._commitSilenceMs()) {
          const utteranceDur = now - this._pcmUtteranceStartAt;
          const sinceLastVoice = Math.max(0, now - (this._pcmLastVoiceAt || now));
          logMicBreadcrumb({ event: "vad_end", ms_silence: sinceLastVoice, gates: getGateSnapshot() });
          logMicBreadcrumb({
            event: "vad_commit",
            dur_ms: Math.max(0, utteranceDur),
            silence_ms: sinceLastVoice,
            gates: getGateSnapshot(),
            policy: { input: "pcm_16k", mode: "pcm16" },
          });
          this._sendCommitControl({ reason: "vad_silence", dur_ms: utteranceDur, silence_ms: sinceLastVoice });
          this._pcmCommitSent = true; this._pcmUtteranceStartAt = null; this._pcmSilenceStartAt = null; this._pcmLastVoiceAt = null;
        }
      }

      // Don’t send if silent or gate closed/muted
      if (silent || !this._sendGate || this._sendMuted) return;

      if (!this._headerSent) {
        this._sendAudioHeader();
        if (!this._headerSent) return;
      }

      const socket = this._ws;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;

      try {
        socket.send(ab);
      } catch (err) {
        try {
          const micOutcome = window.__MIC_OUTCOME;
          window.__logMic?.({ outcome: (micOutcome && micOutcome.ERROR_WS_SEND) || "error_ws_send", message: err?.message });
          window.__logStage?.("client.audio", { outcome: "error", message: err?.message });
        } catch {}
        return;
      }

      // Client-side counters & breadcrumbs
      try {
        if (typeof window.__micChunks === "number") {
          if (window.__micChunks === 0) {
            window.__micChunks = 1;
            window.__micBytes += packetLen;
            const firstChunkMs = Math.max(0, Date.now() - (window.__micArmedAt || 0));
            const micOutcome = window.__MIC_OUTCOME;
            window.__logMic?.({ outcome: (micOutcome && micOutcome.STREAMING) || "streaming", first_chunk_ms: firstChunkMs });
            window.__logStage?.("client.audio", { outcome: "packet_sent", packet_bytes: packetLen, send_q_len: socket.bufferedAmount });
          } else {
            window.__micChunks += 1;
            window.__micBytes += packetLen;
            if ((window.__micChunks % 50) === 0) {
              const micOutcome = window.__MIC_OUTCOME;
              window.__logMic?.({ outcome: (micOutcome && micOutcome.STREAMING_HEARTBEAT) || "streaming_heartbeat" });
            }
          }
        }
      } catch {}

      try { console.info("diag=audio_chunk_sent bytes=%d seq=%d rms=%d", packetLen, this._pcmFrameSeq, Math.round(rms)); } catch {}
      this._pcmFrameSeq += 1;

      if (!this._pcmLastTelemetryAt || (now - this._pcmLastTelemetryAt) >= PCM_TELEMETRY_INTERVAL_MS) {
        this._pcmLastTelemetryAt = now;
        this._logMicFrame(rms, packetLen);
      }
    }

    _sendCommitControl(detail) {
      try {
        window.WSClient?.send?.({
          type: "client.telemetry",
          event: "EVT_AUDIO_CHUNK_SENT_CLIENT",
          meta: { commit: true, reason: detail.reason, dur_ms: detail.dur_ms, silence_ms: detail.silence_ms },
        });
      } catch {}
    }

    _logMicFrame(rms, bytes) {
      const seq = this._pcmFrameSeq;
      const message = `frame seq=${seq} rms=${(Number.isFinite(rms) ? rms : 0).toFixed(1)} bytes=${Number(bytes)||0}`;
      logMicEventString(message);
    }

    // ===== Lifecycle =====
    async _ensureArmed() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        window.__logMic?.({ outcome: window.__MIC_OUTCOME?.ERROR_NO_DEVICE || "error_no_device", message: "media_devices_unavailable" });
        throw new Error("media_devices_unavailable");
      }
      if (!this._stream) {
        if (!this._armingPromise) {
          const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
          this._armingPromise = (async () => {
            try {
              const stream = await navigator.mediaDevices.getUserMedia(constraints);
              this._stream = stream;
              const summary = currentInputDeviceSummary(stream);
              window.__logMic?.({ outcome: window.__MIC_OUTCOME?.PERM_GRANTED || "perm_granted", perm: "granted", device: summary });
              console.info("diag=mic_armed");
              try { if (!this._wakeInit) { WakeWord.init?.(stream); this._wakeInit = true; } } catch {}
            } catch (err) {
              const denied = err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
              window.__logMic?.({
                outcome: denied ? (window.__MIC_OUTCOME?.ERROR_DENIED || "error_denied")
                                : (window.__MIC_OUTCOME?.ERROR_GUM || "error_getuser_media"),
                perm: denied ? "denied" : "error",
                message: err?.message,
              });
              throw err;
            } finally { this._armingPromise = null; }
          })();
        }
        await this._armingPromise;
      }
      if (!this._stream) throw new Error("media_stream_unavailable");
      await this._ensurePcmGraph();
    }

    _updateRecorderState(active, reason) {
      const prevActive = !!this._active;
      const nextActive = !!active;
      this._active = nextActive;

      try {
        if (this._state) {
          if (typeof this._state.setState === "function") this._state.setState({ recorder: { active: nextActive } });
          this._state.recorder = { active: nextActive };
          this._state.emit?.(nextActive ? "recordingStarted" : "recordingStopped", { active: nextActive, reason });
        }
      } catch {}

      emitEvent(CLIENT_HUD_STATE_EVENT, { type: CLIENT_HUD_STATE_EVENT, meta: { state: nextActive ? "Listening" : "Idle", source: "client" } });

      if (!nextActive) window.__logMic?.({ outcome: window.__MIC_OUTCOME?.STOPPED || "stopped", reason, source: "recorder_state" });
      if (!nextActive && this._micOpenEmitted) this._micOpenEmitted = false;
      if (nextActive && !this._micOpenEmitted) {
        this._micOpenEmitted = true;
        emitEvent(CLIENT_MIC_OPEN_EVENT, { type: CLIENT_MIC_OPEN_EVENT, ts: Date.now(), vendor: "pcm16" });
      }

      if (nextActive && !prevActive) {
        const timeslice = this._captureTimesliceMs();
        logMicEventString(`evt=mic_start timeslice_ms=${timeslice===null ? "unknown" : timeslice}`);
      } else if (!nextActive && prevActive) {
        const reasonLabel = (typeof reason === "string" && reason) ? reason : "unknown";
        logMicEventString(`evt=mic_stop reason=${reasonLabel}`);
        this._lastStopLog = { reason: reasonLabel, seq: Math.max(0, this._pcmFrameSeq - (this._pcmFrameSeq > 0 ? 1 : 0)), ts: Date.now() };
      }
    }

    async start(policy = {}) {
      this.setPolicy(policy);
      await this._ensureArmed();
      return this._stream;
    }
    async startMicCaptureIfIdle(policy = {}) {
      this.setPolicy(policy);
      const remaining = getRearmCooldownRemainingMs();
      if (remaining > 0) { logRearmBlocked("startMicCaptureIfIdle", remaining); return false; }
      await this._ensureArmed();
      return true;
    }
    async startListening(policy = {}) {
      this.setPolicy(policy);
      await waitForRearmCooldown("startListening");
      await this._ensureArmed();
      console.info("diag=recorder_mode mode=pcm16");

      // Send header immediately (JSON) with explicit codec
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
      if (this._sendGate) console.info("diag=send_gate_closed reason=%s", reason);
      this._sendGate = false;
      this._updateRecorderState(false, reason);
      this._micOpenEmitted = false;
      this._setSendMuted(false, reason);
      this._resetPcmState();
    }
    handleStopListening(opts = {}) { this.stopListening(opts); }
    handleTtsStart() {
      const stopOnTts = !!this._recorderPolicy()?.stop_on_tts_start;
      if (stopOnTts) { this.stopListening({ reason: "tts_active" }); return; }
      if (this._recorderPolicy()?.mute_send_during_tts !== false) this._setSendMuted(true, "tts_active");
    }
    handleTtsEnd() { this._setSendMuted(false, "tts_end"); }
    handleWsClose() { this._headerSent = false; this._audioHeaderSignature = null; this.endSession(); }
    stop() { this.endSession(); }

    endSession() {
      this._sendGate = false;
      this._setSendMuted(false, "session_end");
      this._updateRecorderState(false, "session_end");
      this._headerSent = false;
      this._audioHeaderSignature = null;
      if (this._pcmNode || this._pcmSource || this._pcmGainNode) { try { this._teardownPcmGraph(); } catch {} }
      this._micOpenEmitted = false; this._active = false;
      if (this._audioContext) { try { if (this._audioContext.state !== "closed") this._audioContext.close(); } catch {} this._audioContext = null; }
      if (this._stream) { try { this._stream.getTracks?.().forEach((t)=>t.stop?.()); } catch {} this._stream = null; }
      this._lastStopLog = null;
    }

    getLastFrameSeq() { return Number.isFinite(this._pcmFrameSeq) ? (this._pcmFrameSeq > 0 ? this._pcmFrameSeq - 1 : 0) : null; }
    didLogMicStop(reason) {
      if (!this._lastStopLog) return false;
      if (reason && this._lastStopLog.reason !== reason) return false;
      const dt = Date.now() - (this._lastStopLog.ts || 0);
      return dt >= 0 && dt <= 1000;
    }
    supportsMicLifecycleTelemetry() { return true; }

    _setSendMuted(muted, reason) {
      const next = !!muted;
      if (this._sendMuted === next) return;
      this._sendMuted = next;
      const label = next ? "diag=send_gate_muted" : "diag=send_gate_unmuted";
      try { console.info(`${label} reason=%s`, (reason || "policy")); } catch {}
    }

    _sendAudioHeader() {
      if (this._headerSent) return true;
      const socket = this._ws; if (!socket) return false;
      if (socket.readyState === WebSocket.CONNECTING) {
        const onOpen = () => { try { socket.removeEventListener("open", onOpen); } catch {} this._sendAudioHeader(); };
        try { socket.addEventListener("open", onOpen, { once: true }); } catch { socket.addEventListener("open", onOpen); }
        return false;
      }
      if (socket.readyState !== WebSocket.OPEN) return false;

      // Explicit codec; Speechmatics open expects raw PCM and we declare pcm_s16le
      const payload = { type: "audio.header", format: "pcm", codec: "pcm_s16le", sample_rate: PCM_SAMPLE_RATE, channels: 1 };
      try {
        socket.send(JSON.stringify(payload));
        this._headerSent = true;
        console.info("diag=audio_header_sent format=pcm codec=pcm_s16le sample_rate=%d channels=%d", payload.sample_rate, payload.channels);
        return true;
      } catch (err) {
        console.warn("diag=audio_header_send_failed %o", err);
        return false;
      }
    }
  }

  if (typeof AudioRecorder._pcmWorkletUrl === "undefined") AudioRecorder._pcmWorkletUrl = null;

  // Bind instance globally (keeps existing integration points intact)
  const initialWs = typeof window !== "undefined" ? window.ws || null : null;
  const recorder = new AudioRecorder(initialWs, window.AppState || null);

  // WS lifecycle glue
  if (typeof window !== "undefined") {
    window.addEventListener("ws.close", () => {
      try { recorder.handleWsClose(); } catch (err) { console.warn("AudioRecorder ws.close handler failed", err); }
    });
  }

  window.AudioRecorder = recorder;
})();
