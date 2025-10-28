(() => {
  const CLIENT_MIC_OPEN_EVENT = "EVT_CLIENT_MIC_OPEN";
  const CLIENT_HUD_STATE_EVENT = "EVT_HUD_STATE";
  const OPUS_MIME = "audio/webm;codecs=opus";
  const MIN_TIMESLICE_MS = 20;
  const DEFAULT_TIMESLICE_MS = 200;

  function emitCustomEvent(type, detail) {
    try {
      window.dispatchEvent(new CustomEvent(type, { detail }));
    } catch (err) {
      console.warn("AudioRecorder event dispatch failed", type, err);
    }
  }

  class AudioRecorder {
    constructor() {
      this._policy = {};
      this._stream = null;
      this._rec = null;
      this._state = "idle";
      this._mask = false;
      this._headerSent = false;
      this._micChunksSent = 0;
      this._micOpenEmitted = false;
      this._format = null;
      this._log = window.console || {};
      this._hud = window?.DiagHUD || window?.DiagHud || null;
    }

    setPolicy(policyObj) {
      this._policy = policyObj || {};
      this._log?.info?.("rec_policy_loaded", this._policy);
    }

    policy() {
      return this._policy || {};
    }

    async start() {
      if (this._state === "error") {
        throw new Error("AudioRecorder unavailable");
      }
      if (this._stream) {
        return this._stream;
      }
      if (!navigator?.mediaDevices?.getUserMedia) {
        this._state = "error";
        const err = new Error("Media capture not supported");
        this._log?.error?.("rec=getusermedia_unsupported");
        throw err;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: false
          }
        });
        this._stream = stream;
        this._state = "idle";
        return stream;
      } catch (err) {
        this._state = "error";
        this._log?.error?.("rec=getusermedia_failed %o", err);
        throw err;
      }
    }

    stop() {
      this._teardownRecorder();
      if (this._stream) {
        try {
          for (const track of this._stream.getTracks()) {
            track.stop();
          }
        } catch (err) {
          this._log?.warn?.("rec=stop_tracks_failed %o", err);
        }
        this._stream = null;
      }
      this._mask = false;
      this._format = null;
      this._state = this._state === "error" ? "error" : "idle";
      this._micOpenEmitted = false;
      this._emitHudState("Idle");
    }

    handleWsClose() {
      this.stop();
    }

    startListening() {
      return this.startMicCaptureIfIdle();
    }

    handleStartListening() {
      return this.startMicCaptureIfIdle();
    }

    async startMicCaptureIfIdle() {
      if (this._state === "error") {
        return false;
      }
      if (!this._stream) {
        try {
          await this.start();
        } catch (err) {
          return false;
        }
      }
      if (!this._stream) {
        return false;
      }
      if (this._rec && this._rec.state === "recording") {
        return true;
      }
      const ok = this._setupRecorderFromPolicy();
      if (!ok) {
        return false;
      }
      this._emitMicOpen();
      this._emitHudState("Listening");
      this._state = "recording";
      return true;
    }

    handleTtsStart() {
      if ((this.policy().capture || {}).mask_during_tts) {
        this._mask = true;
        try {
          this._rec?.pause?.();
        } catch (err) {
          this._log?.warn?.("rec=pause_failed %o", err);
        }
      }
    }

    handleTtsEnd() {
      this._mask = false;
      if (this._rec?.state === "paused") {
        try {
          this._rec.resume();
        } catch (err) {
          this._log?.warn?.("rec=resume_failed %o", err);
        }
      }
    }

    _setupRecorderFromPolicy() {
      if (!this._stream) {
        return false;
      }
      const mp = this.policy().media || {};
      const cp = this.policy().capture || {};
      const supported = !!(window.MediaRecorder && MediaRecorder.isTypeSupported(OPUS_MIME));

      if (mp.asr_input === "webm_opus") {
        if (!supported) {
          this._log?.error?.("rec=webm_opus_unsupported no_pcm_fallback=true");
          this._hud?.banner?.(
            "This browser does not support WebM/Opus. Voice capture disabled.",
            "error"
          );
          this._state = "error";
          return false;
        }
        try {
          this._rec = new MediaRecorder(this._stream, { mimeType: OPUS_MIME });
        } catch (err) {
          this._log?.error?.("rec=media_recorder_ctor_failed %o", err);
          this._hud?.banner?.("Failed to start mic recorder.", "error");
          this._state = "error";
          return false;
        }

        this._rec.ondataavailable = (event) => {
          this._onWebmData(event);
        };
        this._rec.onerror = (event) => {
          this._log?.error?.("rec=media_recorder_error %o", event);
        };
        this._rec.onstop = () => {
          this._rec = null;
          this._state = "idle";
        };

        const slice = Math.max(MIN_TIMESLICE_MS, Number(cp.timeslice_ms ?? DEFAULT_TIMESLICE_MS) || DEFAULT_TIMESLICE_MS);
        this._micChunksSent = 0;
        this._headerSent = false;
        const sampleRate = Number(mp.asr_rate_hz) || 48000;
        const channels = Number(mp.asr_channels) || 1;
        this._format = { format: "opus", sample_rate: sampleRate, channels };
        this._sendAudioHeader(this._format);
        try {
          this._rec.start(slice);
        } catch (err) {
          this._log?.error?.("rec=media_recorder_start_failed %o", err);
          this._hud?.banner?.("Failed to start mic recorder.", "error");
          this._state = "error";
          return false;
        }
        this._log?.info?.("rec=webm_opus_started timeslice_ms=%d", slice);
        return true;
      }

      this._log?.error?.("rec=policy_media_unsupported input=%s", mp.asr_input);
      this._hud?.banner?.("Voice capture policy not supported on this client.", "error");
      this._state = "error";
      return false;
    }

    async _onWebmData(event) {
      if (!event?.data || event.data.size === 0) {
        return;
      }
      if (this._mask) {
        return;
      }
      const ws = this._getWsClient();
      if (!ws) {
        return;
      }
      if (!this._headerSent && this._format) {
        this._sendAudioHeader(this._format);
        if (!this._headerSent) {
          return;
        }
      }
      try {
        const buffer = await event.data.arrayBuffer();
        ws.sendBinary(new Uint8Array(buffer), { lane: "mic", dropIfBusy: false });
        this._micChunksSent += 1;
      } catch (err) {
        this._log?.warn?.("rec=webm_chunk_send_failed %o", err);
      }
    }

    _sendAudioHeader(info) {
      if (!info) {
        return;
      }
      const ws = this._getWsClient();
      if (!ws || typeof ws.send !== "function") {
        return;
      }
      const frame = {
        type: "audio.header",
        format: info.format,
        sample_rate: info.sample_rate,
        channels: info.channels,
        seq_start: 0
      };
      try {
        ws.send(frame);
        this._headerSent = true;
      } catch (err) {
        this._log?.warn?.("rec=audio_header_send_failed %o", err);
      }
    }

    _getWsClient() {
      const ws = window.WSClient;
      if (!ws || typeof ws.sendBinary !== "function") {
        return null;
      }
      if (typeof ws.isConnected === "function" && !ws.isConnected()) {
        return null;
      }
      return ws;
    }

    _emitMicOpen() {
      if (this._micOpenEmitted) {
        return;
      }
      this._micOpenEmitted = true;
      const detail = {
        type: CLIENT_MIC_OPEN_EVENT,
        ts: Date.now(),
        vendor: "webm_opus"
      };
      emitCustomEvent(CLIENT_MIC_OPEN_EVENT, detail);
    }

    _emitHudState(state) {
      emitCustomEvent(CLIENT_HUD_STATE_EVENT, {
        type: CLIENT_HUD_STATE_EVENT,
        meta: { state, source: "client" }
      });
    }

    _teardownRecorder() {
      if (this._rec) {
        try {
          if (this._rec.state !== "inactive") {
            this._rec.stop();
          }
        } catch (err) {
          this._log?.warn?.("rec=media_recorder_stop_failed %o", err);
        }
        this._rec.ondataavailable = null;
        this._rec.onerror = null;
        this._rec.onstop = null;
        this._rec = null;
      }
      this._headerSent = false;
      this._micChunksSent = 0;
      this._format = null;
    }
  }

  const recorder = new AudioRecorder();

  window.addEventListener("ws.close", () => {
    try {
      recorder.handleWsClose();
    } catch (err) {
      console.warn("AudioRecorder ws.close handler failed", err);
    }
  });

  window.AudioRecorder = recorder;
})();
