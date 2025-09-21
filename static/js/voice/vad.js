// static/js/voice/vad.js
// Lightweight RMS-based VAD tuned for conversational UX.
// Features:
//  • Start/stop thresholds with minimum durations
//  • Echo-aware start gating (boost thresholds while TTS is playing)
//  • Emits onSpeechStart / onSpeechEnd callbacks
//  • Safe timers; idempotent start/stop

/** @typedef {{
 *   startRms: number,         // baseline start threshold (RMS)
 *   stopRms: number,          // baseline stop threshold (RMS)
 *   minSpeechMs: number,      // how long RMS must stay >= startRms to commit start
 *   minSilenceMs: number,     // how long RMS must stay <  stopRms to commit end
 *   pollMs: number,           // VAD polling interval
 *   echoBoostStart: number,   // multiplier on startRms while echoStateFn() is true
 *   echoBoostStop: number,    // multiplier on stopRms  while echoStateFn() is true
 *   echoStateFn?: ()=>boolean // returns true when TTS is playing
 * }} VADOptions
 */

/** @typedef {{
 *   onSpeechStart?: ()=>void,
 *   onSpeechEnd?: ()=>void,
 * }} VADCallbacks
 */

export class VAD {
  /**
   * @param {AnalyserNode} analyser
   * @param {VADOptions} opts
   * @param {VADCallbacks} cbs
   */
  constructor(analyser, opts, cbs) {
    this.analyser = analyser;
    this.opts = Object.assign({
      startRms: 0.015,       // ~ -36 dBFS
      stopRms: 0.010,        // ~ -40 dBFS
      minSpeechMs: 220,
      minSilenceMs: 420,
      pollMs: 33,
      echoBoostStart: 1.5,
      echoBoostStop: 1.3,
      echoStateFn: null,
    }, opts || {});
    this.cbs = cbs || {};
    this._buf = new Float32Array(this.analyser.fftSize || 2048);
    this._timer = null;
    this._recording = false;
    this._aboveSince = 0;
    this._belowSince = 0;
  }

  _rms() {
    this.analyser.getFloatTimeDomainData(this._buf);
    let sum = 0;
    for (let i = 0; i < this._buf.length; i++) {
      const v = this._buf[i];
      sum += v * v;
    }
    return Math.sqrt(sum / this._buf.length);
  }

  start() {
    this.stop(); // idempotent
    this._aboveSince = 0;
    this._belowSince = 0;
    const { pollMs } = this.opts;

    this._timer = setInterval(() => {
      const rms = this._rms();
      const now = performance.now();
      const echo = this.opts.echoStateFn ? !!this.opts.echoStateFn() : false;

      const startR = this.opts.startRms * (echo ? this.opts.echoBoostStart : 1);
      const stopR  = this.opts.stopRms  * (echo ? this.opts.echoBoostStop  : 1);

      if (!this._recording) {
        if (rms >= startR) {
          if (!this._aboveSince) this._aboveSince = now;
          if (now - this._aboveSince >= this.opts.minSpeechMs) {
            this._recording = true;
            this._belowSince = 0;
            try { this.cbs.onSpeechStart && this.cbs.onSpeechStart(); } catch {}
          }
        } else {
          this._aboveSince = 0;
        }
      } else {
        if (rms < stopR) {
          if (!this._belowSince) this._belowSince = now;
          if (now - this._belowSince >= this.opts.minSilenceMs) {
            this._recording = false;
            this._aboveSince = 0;
            try { this.cbs.onSpeechEnd && this.cbs.onSpeechEnd(); } catch {}
          }
        } else {
          this._belowSince = 0;
        }
      }
    }, pollMs);
  }

  stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this._recording = false;
    this._aboveSince = 0;
    this._belowSince = 0;
  }

  isRecording() { return this._recording; }
}

