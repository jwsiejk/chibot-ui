import { WakeWord } from "./wake_word.js";

(() => {
  const SEND_TIMESLICE_MS = 300;
  const OPUS_MIME = "audio/webm;codecs=opus";
  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";

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

    async _ensureArmed() {
      if (!navigator?.mediaDevices?.getUserMedia) {
        throw new Error("media_devices_unavailable");
      }
      if (!this._stream) {
        this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        console.info("diag=mic_armed");
        if (!this._wakeInit) {
          try {
            WakeWord.init(this._stream);
          } catch {}
          this._wakeInit = true;
        }
      }
      if (!window.MediaRecorder) {
        throw new Error("media_recorder_unavailable");
      }
      if (typeof MediaRecorder.isTypeSupported === "function" && !MediaRecorder.isTypeSupported(OPUS_MIME)) {
        throw new Error("media_recorder_unsupported");
      }
      if (!this._rec) {
        this._rec = new MediaRecorder(this._stream, { mimeType: OPUS_MIME });
        this._rec.addEventListener("dataavailable", async (event) => {
          if (!event?.data || event.data.size === 0) {
            return;
          }
          const buf = await event.data.arrayBuffer();
          if (!buf || buf.byteLength === 0) {
            return;
          }
          if (!this._sendGate) {
            return;
          }
          const socket = this._ws;
          if (!socket || socket.readyState !== WebSocket.OPEN) {
            return;
          }
          try {
            socket.send(buf);
            console.info("diag=audio_chunk_sent bytes=%d", buf.byteLength);
          } catch (err) {
            console.warn("diag=audio_chunk_send_failed %o", err);
          }
        });
        this._rec.addEventListener("stop", () => {
          this._rec = null;
        });
        this._rec.addEventListener("error", (event) => {
          console.warn("diag=media_recorder_error %o", event);
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
      return this.startListening(policy);
    }

    async handleStartListening(policy = {}) {
      return this.startListening(policy);
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
      return true;
    }

    stopListening(opts = {}) {
      if (this._sendGate) {
        this._sendGate = false;
        const reason = typeof opts?.reason === "string" && opts.reason ? opts.reason : "stop_listening";
        console.info("diag=send_gate_closed reason=%s", reason);
        this._updateRecorderState(false, reason);
      }
    }

    handleStopListening(opts = {}) {
      this.stopListening(opts);
    }

    handleTtsStart() {
      this.stopListening({ reason: "tts_active" });
    }

    handleTtsEnd() {
      // No automatic restart; server will instruct when to resume.
    }

    handleWsClose() {
      this.endSession();
    }

    stop() {
      this.endSession();
    }

    endSession() {
      this._sendGate = false;
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
