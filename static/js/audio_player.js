
// ChunkedAudioPlayer — plays 'audio/webm;codecs=opus' chunks via MediaSource
export class ChunkedAudioPlayer {
  constructor(audioEl) {
    this.audioEl = audioEl;
    this.mediaSource = null;
    this.sourceBuffer = null;
    this.queue = [];
    this.initialized = false;
    this.mime = 'audio/webm; codecs="opus"';
  }
  _initOnce() {
    if (this.initialized) return;
    this.mediaSource = new MediaSource();
    this.audioEl.src = URL.createObjectURL(this.mediaSource);
    this.mediaSource.addEventListener('sourceopen', () => {
      if (!MediaSource.isTypeSupported(this.mime)) {
        console.warn("MSE type not supported:", this.mime);
        return;
      }
      this.sourceBuffer = this.mediaSource.addSourceBuffer(this.mime);
      this.sourceBuffer.mode = 'sequence';
      this.sourceBuffer.addEventListener('updateend', () => this._dequeue());
      this._dequeue();
    });
    this.initialized = true;
  }
  start() {
    this._initOnce();
    // Autoplay if allowed
    const p = this.audioEl.play();
    if (p && p.catch) p.catch(()=>{});
  }
  append(chunk) {
    this._initOnce();
    this.queue.push(chunk);
    this._dequeue();
  }
  _dequeue() {
    if (!this.sourceBuffer || this.sourceBuffer.updating) return;
    if (this.queue.length === 0) return;
    const chunk = this.queue.shift();
    try {
      this.sourceBuffer.appendBuffer(new Uint8Array(chunk));
    } catch (e) {
      console.warn("appendBuffer error", e);
    }
  }
  stop(fadeMs=0) {
    try { this.audioEl.pause(); } catch {}
    try { this.queue.length = 0; } catch {}
    // We intentionally keep MediaSource for re-use within a turn
  }
}
