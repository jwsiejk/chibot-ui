// ChunkedAudioPlayer — plays streamed audio via MediaSource (dynamic MIME)
// Production-optimized: queue-based, safe re-init, and graceful end-of-stream.
export class ChunkedAudioPlayer {
  constructor(audioEl, mime = 'audio/webm; codecs="opus"') {
    this.audioEl = audioEl;

    // MSE state
    this.mediaSource = null;
    this.sourceBuffer = null;
    this.objectUrl = null;

    // Stream state
    this.queue = [];
    this.initialized = false;
    this.mime = mime;
    this.endingRequested = false; // logical end requested; endOfStream after queue drains
    this._pendingReinit = false;

    // Bound handlers
    this._onSourceOpen = this._onSourceOpen.bind(this);
    this._onUpdateEnd  = this._onUpdateEnd.bind(this);
  }

  // --- Public API -----------------------------------------------------------

  setMime(mime) {
    if (!mime || this.mime === mime) return;
    // Recreate pipeline if MIME changes; keep any queued audio
    this.mime = mime;
    this._reinitPipeline({ keepQueue: true });
  }

  appendBytes(bytes) {
    const chunk = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    this.queue.push(chunk);
    this._initOnce();
    this._pump();
    this._ensurePlayUnlocked();
  }

  appendBase64(b64) {
    if (!b64) return;
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    this.appendBytes(out);
  }

  // Optional: mark the natural end of the stream.
  // We will call mediaSource.endOfStream() after the queue is fully drained.
  end() {
    this.endingRequested = true;
    this._pump();
  }

  // Maintain legacy behavior: pause and drop any pending chunks without tearing down MSE.
  stop(/* fadeMs=0 */) {
    try { this.audioEl.pause(); } catch {}
    try { this.queue.length = 0; } catch {}
    // Do NOT tear down media source here to avoid regressions.
    // If you need a hard reset elsewhere, call teardown().
  }

  // Hard cleanup (not called by default to avoid regressions)
  teardown() {
    try { this.audioEl.pause(); } catch {}
    try {
      if (this.sourceBuffer && this.mediaSource && this.mediaSource.readyState === 'open') {
        try { this.mediaSource.removeSourceBuffer(this.sourceBuffer); } catch {}
      }
    } catch {}
    try {
      if (this.mediaSource && this.mediaSource.readyState === 'open') {
        try { this.mediaSource.endOfStream(); } catch {}
      }
    } catch {}

    if (this.mediaSource) {
      try { this.mediaSource.removeEventListener('sourceopen', this._onSourceOpen); } catch {}
    }
    if (this.sourceBuffer) {
      try { this.sourceBuffer.removeEventListener('updateend', this._onUpdateEnd); } catch {}
    }

    this.sourceBuffer = null;
    this.mediaSource  = null;
    this.initialized  = false;
    this.endingRequested = false;

    if (this.objectUrl) {
      try { URL.revokeObjectURL(this.objectUrl); } catch {}
      this.objectUrl = null;
    }
    // Keep queue intact by default; callers can clear it via stop()
  }

  // --- Internal -------------------------------------------------------------

  _initOnce() {
    if (this.initialized && this.mediaSource && this.sourceBuffer) return;
    if (!('MediaSource' in window)) {
      console.warn('[audio] MediaSource unsupported');
      return;
    }

    // If a previous MSE exists but is closed or invalid, recreate it.
    if (this.mediaSource && this.mediaSource.readyState === 'closed') {
      this._reinitPipeline({ keepQueue: true });
      return;
    }
    if (!this.mediaSource) {
      this.mediaSource = new MediaSource();
      this.mediaSource.addEventListener('sourceopen', this._onSourceOpen, { once: true });
      this.objectUrl = URL.createObjectURL(this.mediaSource);
      this.audioEl.src = this.objectUrl;
    }
    // sourceBuffer will be created in _onSourceOpen
  }

  _onSourceOpen() {
    try {
      if (!MediaSource.isTypeSupported(this.mime)) {
        console.warn('[audio] MIME not supported by MSE:', this.mime);
      }
      this.sourceBuffer = this.mediaSource.addSourceBuffer(this.mime);
      this.sourceBuffer.addEventListener('updateend', this._onUpdateEnd);
      this.initialized = true;
      this._pump();
    } catch (e) {
      console.warn('[audio] addSourceBuffer failed', this.mime, e);
      // If MIME failed, fallback softly by recreating with same MIME (may have transient state)
      this._recoverFromError(e);
    }
  }

  _onUpdateEnd() {
    this._pump();
  }

  _pump() {
    // If we’re not ready, nothing to do yet.
    if (!this.mediaSource || !this.sourceBuffer) return;

    // If SB busy, wait for updateend
    if (this.sourceBuffer.updating) return;

    // Drain queue
    if (this.queue.length > 0) {
      const chunk = this.queue.shift();
      try {
        this.sourceBuffer.appendBuffer(chunk);
      } catch (e) {
        // Robust recovery: if buffer/source was removed/ended, recreate and retry.
        this._recoverFromError(e, chunk);
      }
      return; // append triggers updateend -> _pump again
    }

    // No more queued data: if logical end requested, close the stream cleanly.
    if (this.endingRequested && this.mediaSource.readyState === 'open') {
      try { this.mediaSource.endOfStream(); } catch {}
      this.endingRequested = false; // one-shot
    }
  }

  _recoverFromError(e, firstChunkToReplay = null) {
    const msg = String(e && (e.message || e));
    const shouldReinit =
      msg.includes('removed from the parent media source') ||  // your observed error
      msg.includes('detached') ||
      msg.includes('ended') ||
      (this.mediaSource && this.mediaSource.readyState === 'closed') ||
      !this.sourceBuffer;

    console.warn('[audio] appendBuffer error, attempting soft reinit:', msg);

    if (!shouldReinit || this._pendingReinit) return;

    this._pendingReinit = true;

    // Put the failed chunk back at the front of the queue to replay after re-init
    if (firstChunkToReplay) this.queue.unshift(firstChunkToReplay);

    // Recreate pipeline but keep the buffered queue
    this._reinitPipeline({ keepQueue: true });

    // Allow a tick for MSE to open before we resume pumping
    setTimeout(() => {
      this._pendingReinit = false;
      this._pump();
      this._ensurePlayUnlocked();
    }, 0);
  }

  _reinitPipeline({ keepQueue } = { keepQueue: true }) {
    const savedQueue = keepQueue ? this.queue.slice(0) : [];
    this.teardown();
    this.queue = savedQueue;
    this._initOnce();
  }

  _ensurePlayUnlocked() {
    try {
      if (this.audioEl && this.audioEl.paused) {
        // Autoplay can be blocked; Start button should have unlocked it already.
        this.audioEl.play().catch(() => {});
      }
    } catch {}
  }
}
