import { WakeWord } from "./wake_word.js";

/** POLICY: MediaRecorder only in audio_recorder.js; no PTT; no manual barge-in; wake-word only. */
(() => {
  const SEND_TIMESLICE_MS = 300;
  const OPUS_MIME = "audio/webm;codecs=opus";
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
    }

    setSocket(ws) {
      this._ws = ws || null;
    }

    setPolicy(policy) {
      if (policy && typeof policy === "object") {
        this._policy = policy;
      } else {
        this._policy = {};
      }
    }

    get policy() {
      return this._policy || {};
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

    async _ensureArmed() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        logMic?.({ outcome: (micOutcome && micOutcome.ERROR_NO_DEVICE) || 'error_no_device', message: 'media_devices_unavailable' });
        throw new Error("media_devices_unavailable");
      }
      if (!this._stream) {
        try {
          this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
      this._active = Boolean(active);
      const payload = { active: this._active };
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
      if (!this._active) {
        const micOutcome = typeof window !== "undefined" ? window.__MIC_OUTCOME : null;
        const logMic = typeof window !== "undefined" ? window.__logMic : null;
        logMic?.({ outcome: (micOutcome && micOutcome.STOPPED) || 'stopped', reason, source: 'recorder_state' });
      }
      if (this._active && !this._micOpenEmitted) {
        this._micOpenEmitted = true;
        emitEvent(CLIENT_MIC_OPEN_EVENT, {
          type: CLIENT_MIC_OPEN_EVENT,
          ts: Date.now(),
          vendor: "webm_opus"
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
      if (this._sendGate) {
        this._sendGate = false;
        const reason = typeof opts?.reason === "string" && opts.reason ? opts.reason : "stop_listening";
        console.info("diag=send_gate_closed reason=%s", reason);
        this._updateRecorderState(false, reason);
      }
      this._setSendMuted(false, typeof opts?.reason === "string" ? opts.reason : "stop_listening");
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
      this.endSession();
    }

    stop() {
      this.endSession();
    }

    endSession() {
      this._sendGate = false;
      this._setSendMuted(false, "session_end");
      this._updateRecorderState(false, "session_end");
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
      if (this._stream) {
        try {
          this._stream.getTracks?.().forEach((track) => track.stop?.());
        } catch {}
        this._stream = null;
      }
    }
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
