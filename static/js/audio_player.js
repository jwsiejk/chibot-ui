// ChunkedAudioPlayer — plays streamed audio via MediaSource (dynamic MIME)
export class ChunkedAudioPlayer {
  constructor(audioEl, mime = 'audio/webm; codecs="opus"') {
    this.audioEl = audioEl;
    this.mediaSource = null;
    this.sourceBuffer = null;
    this.queue = [];
    this.initialized = false;
    this.mime = mime;
  }
  setMime(mime){
    if (this.mime === mime) return;
    // Recreate pipeline if MIME changes
    this.mime = mime;
    this.initialized = false;
    this.mediaSource = null;
    this.sourceBuffer = null;
    this.queue.length = 0;
    this._initOnce();
  }
  _initOnce() {
    if (this.initialized) return;
    if (!('MediaSource' in window)) { console.warn('[audio] MediaSource unsupported'); return; }
    this.mediaSource = new MediaSource();
    this.audioEl.src = URL.createObjectURL(this.mediaSource);
    this.mediaSource.addEventListener('sourceopen', () => {
      try {
        if (!MediaSource.isTypeSupported(this.mime)){
          console.warn('[audio] MIME not supported by MSE:', this.mime);
        }
        this.sourceBuffer = this.mediaSource.addSourceBuffer(this.mime);
        this.sourceBuffer.addEventListener('updateend', () => this._flush());
        this.initialized = true;
        this._flush();
      } catch (e){
        console.warn('[audio] MSE init failed', e);
      }
    });
  }
  appendBytes(bytes){
    this.queue.push(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
    this._initOnce();
    this._flush();
    try { this.audioEl.play().catch(()=>{}); } catch{}
  }
  appendBase64(b64){
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) out[i] = bin.charCodeAt(i);
    this.appendBytes(out);
  }
  _flush(){
    if (!this.sourceBuffer || this.sourceBuffer.updating) return;
    if (this.queue.length === 0) return;
    const chunk = this.queue.shift();
    try {
      this.sourceBuffer.appendBuffer(chunk);
    } catch (e) {
      console.warn('[audio] appendBuffer error', e);
    }
  }
  stop(fadeMs=0) {
    try { this.audioEl.pause(); } catch {}
    try { this.queue.length = 0; } catch {}
  }
}
