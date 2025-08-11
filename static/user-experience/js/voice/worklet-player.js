// voice/worklet-player.js — AudioWorkletProcessor for low-latency streaming PCM
// Registers: "chip-stream-player"
class ChipStreamPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];         // array of Float32Array chunks (mono)
    this.position = 0;       // index within current chunk
    this.channels = 1;
    this.srcRate = sampleRate; // context's rate (rendering)
    this.srcChunkRate = sampleRate; // incoming chunk rate; may differ
    this.port.onmessage = (e) => this._onmsg(e.data);
  }

  _onmsg(msg) {
    const t = msg && msg.type;
    if (t === "start") {
      this.channels = msg.channels || 1;
      this.srcChunkRate = msg.sampleRate || this.srcRate;
      // no-op otherwise
    } else if (t === "pcm_f32") {
      // zero-copy payload (transfer)
      const buf = msg.payload;
      if (buf && buf.byteLength) this.queue.push(new Float32Array(buf));
    } else if (t === "pcm_f32_copy") {
      // copy-based payload (Array)
      const arr = msg.payload || [];
      if (arr.length) this.queue.push(Float32Array.from(arr));
    } else if (t === "end" || t === "flush") {
      this.queue.length = 0;
      this.position = 0;
    }
  }

  /**
   * Very light resample if incoming chunks' sampleRate differs from context.
   * Linear interpolation, mono only for simplicity (frontend converts).
   */
  _readFrames(desired) {
    if (!this.queue.length) return null;
    const inBuf = this.queue[0];
    const inRate = this.srcChunkRate || this.srcRate;
    const outRate = this.srcRate;

    if (inRate === outRate) {
      // Fast path: copy as much as needed
      const remaining = inBuf.length - this.position;
      const take = Math.min(desired, remaining);
      const out = inBuf.subarray(this.position, this.position + take);
      this.position += take;
      if (this.position >= inBuf.length) { this.queue.shift(); this.position = 0; }
      return out;
    }

    const ratio = inRate / outRate;
    const neededIn = Math.ceil(desired * ratio);
    const out = new Float32Array(desired);
    let i = 0;
    while (i < desired) {
      // Source position in input space
      const srcPos = (this.position + i * ratio);
      const srcIdx = Math.floor(srcPos);
      const srcFrac = srcPos - srcIdx;

      const a = inBuf[srcIdx] || 0;
      const b = inBuf[srcIdx + 1] || a;
      out[i++] = a + (b - a) * srcFrac;

      if (srcIdx + 2 >= inBuf.length) {
        // We consumed this buffer; move to next one
        const consumed = srcIdx + 1;
        this.position = 0;
        this.queue.shift();
        if (!this.queue.length) break;
      }
    }
    // Advance input read head approximately
    this.position += neededIn;
    if (this.position >= inBuf.length) { this.queue.shift(); this.position = 0; }
    return out;
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    const ch0 = output[0];

    // Pull exactly the number of frames the engine asks for
    const frames = this._readFrames(ch0.length);

    if (frames && frames.length) {
      const n = ch0.length;
      for (let i = 0; i < n; i++) {
        const s = frames[i] || 0;
        for (let c = 0; c < output.length; c++) output[c][i] = s;
      }
    } else {
      // Underrun: output silence
      for (let c = 0; c < output.length; c++) {
        output[c].fill(0);
      }
    }

    return true; // keep processor alive
  }
}

registerProcessor("chip-stream-player", ChipStreamPlayer);
