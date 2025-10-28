(() => {
  // Minimal, policy-first, Opus-only mic recorder
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
      this._wsClient = null; // preferred explicit binding
      this._log = window.console || {};
      this._hud = window?.DiagHUD || window?.DiagHud || null;
      this._lastMaskChunkAt = 0; // ms timestamp of last keepalive chunk while masked
    }

    /* ---------------- Policy ---------------- */

    setPolicy(policyObj) {
      this._policy = policyObj || {};
      this._log?.info?.("rec_policy_loaded", this._policy);
      // DIAG (if HUD enabled)
      this._sendDiag("rec_policy_loaded", {
        media: this.policy().media || {},
        capture: this.policy().capture || {}
      }, "info");
    }

    policy() { return this._policy || {}; }

    /* ---------------- WebSocket binding & selection ---------------- */

    // Allow app code to explicitly inject the chat WS (best)
    setWsClient(ws) { this._wsClient = ws || null; }

    // Find the right WS: prefer injected; else ChatWSClient; else WSClient.
    // If multiple exist, prefer one whose protocol indicates chat (e.g., 'chat.v2').
    _pickGlobalWs() {
      const candidates = [];
      if (this._wsClient) candidates.push(this._wsClient);
      if (window.ChatWSClient) candidates.push(window.ChatWSClient);
      if (window.WSClient) candidates.push(window.WSClient);

      // de-dup
      const uniq = [];
      const seen = new Set();
      for (const c of candidates) {
        if (!c) continue;
        const key = (c === Object(c)) ? (c.__id || c) : c;
        if (!seen.has(key)) { uniq.push(c); seen.add(key); }
      }
      if (uniq.length === 0) return null;

      // prefer chat protocol
      const withProto = uniq.find(c => {
        try { return (c.protocol === "chat.v2") || (c.getProtocol && c.getProtocol() === "chat.v2"); }
        catch { return false; }
      });
      return withProto || uniq[0];
    }

    _getWsClient() {
      const ws = this._pickGlobalWs();
      if (!ws) return null;
      if (typeof ws.isConnected === "function" && !ws.isConnected()) return null;
      if (typeof ws.sendBinary !== "function") return null; // audio frames
      if (typeof ws.send !== "function") return null;       // audio.header / diag
      return ws;
    }

    /* ---------------- DIAG helpers ---------------- */

    _diagEnabled() {
      const cfg = window && window.__CFG__;
      return !!(cfg && cfg.DIAG_CLIENT_HUD);
    }

    _sendDiag(event, data, level = "info") {
      if (!this._diagEnabled()) return;
      const ws = this._getWsClient();
      if (!ws) return;
      const frame = {
        type: "client.diag",
        event: String(event || "recorder").slice(0, 64),
        ts: Date.now(),
        level: String(level || "info").slice(0, 16),
        data
      };
      try { ws.send(frame); } catch (err) {
        this._log?.warn?.("rec=diag_send_failed %o", err);
      }
    }

    /* ---------------- Public lifecycle ---------------- */

    async start() {
      if (this._state === "error") {
        throw new Error("AudioRecorder unavailable");
      }
      if (this._stream) return this._stream;

      if (!navigator?.mediaDevices?.getUserMedia) {
        this._state = "error";
        this._log?.error?.("rec=getusermedia_unsupported");
        throw new Error("Media capture not supported");
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false }
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
        try { for (const t of this._stream.getTracks()) t.stop(); } catch {}
        this._stream = null;
      }
      this._mask = false;
      this._format = null;
      this._state = (this._state === "error") ? "error" : "idle";
      this._micOpenEmitted = false;
      this._emitHudState("Idle");
    }

    handleWsClose() { this.stop(); }
    startListening() { return this.startMicCaptureIfIdle(); }
    handleStartListening() { return this.startMicCaptureIfIdle(); }

    /* ---------------- Core: idempotent starter ---------------- */

    async startMicCaptureIfIdle() {
      if (this._state === "error") return false;

      if (!this._stream) {
        try { await this.start(); } catch { return false; }
      }
      if (!this._stream) return false;

      if (this._rec && this._rec.state === "recording") return true;

      const ok = this._setupRecorderFromPolicy();
      if (!ok) return false;

      this._emitMicOpen();
      this._emitHudState("Listening");
      this._state = "recording";
      return true;
    }

    /* ---------------- TTS mask ---------------- */

    handleTtsStart() {
      if ((this.policy().capture || {}).mask_during_tts) {
        this._mask = true;
        this._lastMaskChunkAt = 0;
        // DO NOT pause() here; recorder must keep running to generate keepalive chunks.
      }
    }

    handleTtsEnd() {
      this._mask = false;
      if (this._rec?.state === "paused") { try { this._rec.resume(); } catch {} }
    }

    /* ---------------- Recorder setup (Opus-only) ---------------- */

    _setupRecorderFromPolicy() {
      if (!this._stream) return false;
      const mp = this.policy().media || {};
      const cp = this.policy().capture || {};
      const supported = !!(window.MediaRecorder && MediaRecorder.isTypeSupported(OPUS_MIME));

      if (mp.asr_input !== "webm_opus") {
        this._log?.error?.("rec=policy_media_unsupported input=%s", mp.asr_input);
        this._hud?.banner?.("Voice capture policy not supported on this client.", "error");
        this._state = "error";
        return false;
      }
      if (!supported) {
        this._log?.error?.("rec=webm_opus_unsupported no_pcm_fallback=true");
        this._hud?.banner?.("This browser does not support WebM/Opus. Voice capture disabled.", "error");
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

      // One-time header BEFORE first chunk
      const sampleRate = Number(mp.asr_rate_hz) || 48000;
      const channels = Number(mp.asr_channels) || 1;
      this._format = { format: "opus", sample_rate: sampleRate, channels };
      this._sendAudioHeader(this._format); // idempotent via _headerSent

      // Wire events & start with timeslice (CRITICAL)
      const slice = Math.max(MIN_TIMESLICE_MS, Number(cp.timeslice_ms ?? DEFAULT_TIMESLICE_MS) || DEFAULT_TIMESLICE_MS);
      this._micChunksSent = 0;
      this._rec.ondataavailable = (e) => this._onWebmData(e);
      this._rec.onerror = (ev) => this._log?.error?.("rec=media_recorder_error %o", ev);
      this._rec.onstop  = () => { this._rec = null; if (this._state !== "error") this._state = "idle"; };

      try {
        this._rec.start(slice);
      } catch (err) {
        this._log?.error?.("rec=media_recorder_start_failed %o", err);
        this._hud?.banner?.("Failed to start mic recorder.", "error");
        this._state = "error";
        return false;
      }

      this._log?.info?.("rec=webm_opus_started timeslice_ms=%d", slice);
      this._sendDiag("rec=webm_opus_started", { timeslice_ms: slice }, "info");
      return true;
    }

    async _onWebmData(event) {
      if (!event?.data || event.data.size === 0) return;
      if (this._mask) {
        const cap = this.policy().capture || {};
        if (cap.mask_keepalive_enable) {
          const now = Date.now();
          const interval = Math.max(1000, Number(cap.mask_keepalive_ms ?? 5000));
          if (now - this._lastMaskChunkAt < interval) {
            return; // drop most masked chunks
          }
          this._lastMaskChunkAt = now; // allow this one keepalive chunk to pass
        } else {
          return; // fully drop during mask if keepalive disabled
        }
      }
      const ws = this._getWsClient();
      if (!ws) return;

      // Ensure header precedes first chunk (belt & suspenders)
      if (!this._headerSent && this._format) {
        this._sendAudioHeader(this._format);
        if (!this._headerSent) return; // still couldn't send; drop this chunk
      }

      try {
        const buffer = await event.data.arrayBuffer();
        ws.sendBinary(new Uint8Array(buffer), { lane: "mic", dropIfBusy: false });
        this._micChunksSent += 1;
        if (this._micChunksSent % 20 === 0) {
          this._sendDiag("mic_progress", { chunks: this._micChunksSent }, "debug");
        }
      } catch (err) {
        this._log?.warn?.("rec=webm_chunk_send_failed %o", err);
        this._sendDiag("evt=mic_chunk_send_failed",
          err && err.message ? { message: String(err.message).slice(0, 128) } : null,
          "warning");
      }
    }

    _sendAudioHeader(info) {
      if (!info || this._headerSent) return;
      const ws = this._getWsClient();
      if (!ws) return;
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

    /* ---------------- UI signals ---------------- */

    _emitMicOpen() {
      if (this._micOpenEmitted) return;
      this._micOpenEmitted = true;
      emitCustomEvent(CLIENT_MIC_OPEN_EVENT, {
        type: CLIENT_MIC_OPEN_EVENT, ts: Date.now(), vendor: "webm_opus"
      });
    }

    _emitHudState(state) {
      emitCustomEvent(CLIENT_HUD_STATE_EVENT, {
        type: CLIENT_HUD_STATE_EVENT,
        meta: { state, source: "client" }
      });
    }

    /* ---------------- Teardown ---------------- */

    _teardownRecorder() {
      if (this._rec) {
        try { if (this._rec.state !== "inactive") this._rec.stop(); } catch {}
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

  // Stop mic if WS closes
  window.addEventListener("ws.close", () => {
    try { recorder.handleWsClose(); } catch (err) {
      console.warn("AudioRecorder ws.close handler failed", err);
    }
  });

  // Expose in global namespace
  window.AudioRecorder = recorder;
})();
