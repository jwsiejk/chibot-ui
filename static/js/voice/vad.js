// static/js/voice/vad.js
// Lightweight RMS-based VAD tuned for conversational UX.
// Features:
//  • Rolling noise-floor tracking with adaptive thresholds (dB offsets)
//  • Echo-aware gating (raise thresholds while TTS is active)
//  • Emits onSpeechStart / onSpeechEnd callbacks with metrics
//  • Safe timers; idempotent start/stop

import { logIfEnabled } from '../util/logging.js';
import { nowMs } from './core/index.js';

const voiceLog = (level, ...args) => {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
};

const getSessionId = () => {
  try {
    if (typeof window !== 'undefined' && window.__askchip_voice_session_id) {
      return window.__askchip_voice_session_id;
    }
  } catch {}
  return null;
};

const getTurnId = () => {
  try {
    if (typeof window !== 'undefined' && window.__askchip_turn_trace_id != null) {
      return window.__askchip_turn_trace_id;
    }
  } catch {}
  return null;
};

const EPSILON = 1e-8;
const CLAMP_MIN_DB = -120;
const CLAMP_MAX_DB = -10;

const rmsToDb = (rms) => 20 * Math.log10(Math.max(EPSILON, rms));
const dbToRms = (db) => Math.pow(10, db / 20);
const clampDb = (db) => Math.min(CLAMP_MAX_DB, Math.max(CLAMP_MIN_DB, db));
const DEFAULT_SUPPRESS_DB = 15;
const DEFAULT_SIGNATURE_MAX_AGE_MS = 350;

/** @typedef {{
 *   minSpeechMs?: number,
 *   minSilenceMs?: number,
 *   pollMs?: number,
 *   cooldownMs?: number,
 *   startDbOffset?: number,
 *   stopDbOffset?: number,
 *   minStartDb?: number,
 *   minStopDb?: number,
 *   echoBoostStartDb?: number,
 *   echoBoostStopDb?: number,
 *   noiseFloorAlpha?: number,
 *   noiseFloorRiseAlpha?: number,
 *   noiseFloorGuardDb?: number,
 *   noiseFloorHangMs?: number,
 *   initialNoiseFloorDb?: number,
 *   startRms?: number,          // legacy absolute fallback
 *   stopRms?: number,           // legacy absolute fallback
 *   echoStateFn?: ()=>boolean,
 *   gateFn?: ()=>boolean,
 * }} VADOptions
 */

/** @typedef {{
 *   rms?: number,
 *   rmsDb?: number,
 *   noiseFloorDb?: number,
 *   snrDb?: number,
 *   peakDb?: number,
 *   speechDurationMs?: number,
 *   thresholds?: { startDb?: number, stopDb?: number },
 *   reason?: string,
 * }} VADDetail
 */

/** @typedef {{
 *   onSpeechStart?: (detail: VADDetail) => void,
 *   onSpeechEnd?: (detail: VADDetail) => void,
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
      minSpeechMs: 280,
      minSilenceMs: 300,
      pollMs: 33,
      cooldownMs: 380,
      startDbOffset: 10,
      stopDbOffset: 6,
      minStartDb: -65,
      minStopDb: -70,
      echoBoostStartDb: 8,
      echoBoostStopDb: 6,
      noiseFloorAlpha: 0.05,
      noiseFloorRiseAlpha: 0.01,
      noiseFloorGuardDb: 3,
      noiseFloorHangMs: 600,
      initialNoiseFloorDb: -72,
      echoStateFn: null,
      gateFn: null,
    }, opts || {});
    this.cbs = cbs || {};
    this._buf = new Float32Array(this.analyser.fftSize || 2048);
    this._timer = null;
    this._recording = false;
    this._aboveSince = 0;
    this._belowSince = 0;
    this._cooldownUntil = 0;
    this._speechStartedAt = 0;
    this._activeDetail = null;
    this._activeNoiseFloorDb = null;
    this._noiseFloorDb = clampDb(
      Number.isFinite(this.opts.initialNoiseFloorDb)
        ? this.opts.initialNoiseFloorDb
        : -72
    );
    this._lastNoiseUpdate = 0;
    this._legacyStartDb = Number.isFinite(this.opts.startRms) ? rmsToDb(this.opts.startRms) : null;
    this._legacyStopDb = Number.isFinite(this.opts.stopRms) ? rmsToDb(this.opts.stopRms) : null;
    this._suppressingEcho = false;
    this._lastGateLogTs = 0;
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

  _makeDetail(rms, rmsDb, noiseFloorDb, startDb, stopDb) {
    const snrDb = (Number.isFinite(rmsDb) && Number.isFinite(noiseFloorDb))
      ? rmsDb - noiseFloorDb
      : null;
    return {
      rms,
      rmsDb,
      noiseFloorDb,
      snrDb,
      thresholds: { startDb, stopDb },
    };
  }

  _updateNoiseFloor(rmsDb, now, baseStartDb) {
    if (!Number.isFinite(rmsDb)) return;
    if (!Number.isFinite(this._noiseFloorDb)) {
      this._noiseFloorDb = clampDb(rmsDb);
      this._lastNoiseUpdate = now;
      return;
    }

    const guard = Number.isFinite(this.opts.noiseFloorGuardDb) ? this.opts.noiseFloorGuardDb : 3;
    const hangMs = Number.isFinite(this.opts.noiseFloorHangMs) ? this.opts.noiseFloorHangMs : 600;
    const alphaDown = Number.isFinite(this.opts.noiseFloorAlpha) ? this.opts.noiseFloorAlpha : 0.05;
    const alphaUp = Number.isFinite(this.opts.noiseFloorRiseAlpha) ? this.opts.noiseFloorRiseAlpha : 0.01;

    if (rmsDb <= baseStartDb - guard) {
      this._noiseFloorDb = clampDb(this._noiseFloorDb + alphaDown * (rmsDb - this._noiseFloorDb));
      this._lastNoiseUpdate = now;
    } else if (now - this._lastNoiseUpdate >= hangMs) {
      this._noiseFloorDb = clampDb(this._noiseFloorDb + alphaUp * (rmsDb - this._noiseFloorDb));
      this._lastNoiseUpdate = now;
    }
  }

  _computeThresholds(noiseFloorDb, echoActive) {
    const baseStart = clampDb(
      Math.max(
        this.opts.minStartDb ?? -65,
        noiseFloorDb + (this.opts.startDbOffset ?? 10)
      )
    );
    const baseStop = clampDb(
      Math.max(
        this.opts.minStopDb ?? -70,
        noiseFloorDb + (this.opts.stopDbOffset ?? 6)
      )
    );

    let startDb = baseStart;
    let stopDb = baseStop;

    if (Number.isFinite(this._legacyStartDb)) {
      startDb = Math.max(startDb, this._legacyStartDb);
    }
    if (Number.isFinite(this._legacyStopDb)) {
      stopDb = Math.max(stopDb, this._legacyStopDb);
    }

    if (echoActive) {
      startDb += this.opts.echoBoostStartDb ?? 0;
      stopDb += this.opts.echoBoostStopDb ?? 0;
    }

    return {
      startDb: clampDb(startDb),
      stopDb: clampDb(stopDb),
      baseStartDb: baseStart,
    };
  }

  start() {
    this.stop(); // idempotent
    this._aboveSince = 0;
    this._belowSince = 0;
    this._cooldownUntil = 0;
    this._speechStartedAt = 0;
    this._activeDetail = null;
    this._activeNoiseFloorDb = null;
    this._suppressingEcho = false;

    const pollMs = Number.isFinite(this.opts.pollMs) ? this.opts.pollMs : 33;

    this._timer = setInterval(() => this._pollFrame(), pollMs);
  }

  stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this._recording = false;
    this._aboveSince = 0;
    this._belowSince = 0;
    this._cooldownUntil = 0;
    this._speechStartedAt = 0;
    this._activeDetail = null;
    this._activeNoiseFloorDb = null;
    this._suppressingEcho = false;
  }

  isRecording() { return this._recording; }

  _shouldSuppressEcho(rmsDb, now) {
    if (!Number.isFinite(rmsDb)) return false;
    const fn = this.opts.echoSignatureFn;
    if (typeof fn !== 'function') return false;
    let signature;
    try {
      signature = fn();
    } catch {
      return false;
    }
    if (!signature || !Number.isFinite(signature.rmsDb)) return false;
    const requiredGap = Number.isFinite(this.opts.echoSuppressDb)
      ? this.opts.echoSuppressDb
      : DEFAULT_SUPPRESS_DB;
    const maxAge = Number.isFinite(this.opts.echoSignatureMaxAgeMs)
      ? this.opts.echoSignatureMaxAgeMs
      : DEFAULT_SIGNATURE_MAX_AGE_MS;
    const signatureTs = Number.isFinite(signature.timestamp) ? signature.timestamp : null;
    if (signatureTs !== null) {
      const age = now - signatureTs;
      if (Number.isFinite(age) && age > maxAge) {
        this._suppressingEcho = false;
        return false;
      }
    }
    const gap = rmsDb - signature.rmsDb;
    if (!Number.isFinite(gap) || gap < requiredGap) {
      if (!this._suppressingEcho) {
        this._suppressingEcho = true;
        try {
          this.cbs.onSuppressed && this.cbs.onSuppressed({
            micRmsDb: rmsDb,
            echoRmsDb: signature.rmsDb,
            gapDb: gap,
            requiredGapDb: requiredGap,
            signatureTimestamp: signature.timestamp ?? null,
            mfcc: Array.isArray(signature.mfcc) ? signature.mfcc.slice() : undefined,
          });
        } catch {}
      }
      return true;
    }
    this._suppressingEcho = false;
    return false;
  }

  _pollFrame() {
    const rms = this._rms();
    const rmsDb = rmsToDb(rms);
    const now = nowMs();
    const inCooldown = now < this._cooldownUntil;
    const echo = this.opts.echoStateFn ? !!this.opts.echoStateFn() : false;
    const gateAllowed = this.opts.gateFn ? !!this.opts.gateFn() : true;

    const noiseFloorDb = Number.isFinite(this._noiseFloorDb) ? this._noiseFloorDb : rmsDb;
    const { startDb, stopDb, baseStartDb } = this._computeThresholds(noiseFloorDb, echo);
    const startR = dbToRms(startDb);
    const stopR = dbToRms(stopDb);

    if (!this._recording) {
      this._updateNoiseFloor(rmsDb, now, baseStartDb);
    }

    if (!gateAllowed) {
      if (!Number.isFinite(this._lastGateLogTs) || now - this._lastGateLogTs >= 75) {
        voiceLog('info', '[vad] gate blocked this tick', {
          ts_ms: Date.now(),
          session_id: getSessionId(),
          turn_id: getTurnId(),
          reason: 'tts_mask',
        });
        this._lastGateLogTs = now;
      }
      if (this._recording) {
        this._recording = false;
        this._speechStartedAt = 0;
        this._activeDetail = null;
        this._activeNoiseFloorDb = null;
      }
      this._aboveSince = 0;
      this._belowSince = 0;
      this._cooldownUntil = 0;
      this._suppressingEcho = false;
      return;
    }

    this._lastGateLogTs = 0;

    if (!this._recording) {
      if (inCooldown) {
        this._aboveSince = 0;
        this._suppressingEcho = false;
        return;
      }
      if (rms >= startR) {
        if (this._shouldSuppressEcho(rmsDb, now)) {
          this._aboveSince = 0;
          return;
        }
        this._suppressingEcho = false;
        if (!this._aboveSince) this._aboveSince = now;
        if (now - this._aboveSince >= (this.opts.minSpeechMs ?? 0)) {
          this._recording = true;
          this._belowSince = 0;
          this._speechStartedAt = now;
          this._activeDetail = this._makeDetail(rms, rmsDb, noiseFloorDb, startDb, stopDb);
          this._activeNoiseFloorDb = noiseFloorDb;
          try {
            this.cbs.onSpeechStart && this.cbs.onSpeechStart({
              ...this._activeDetail,
              peakDb: this._activeDetail?.rmsDb,
              speechDurationMs: 0,
            });
          } catch {}
        }
      } else {
        this._aboveSince = 0;
        this._suppressingEcho = false;
      }
    } else {
      this._suppressingEcho = false;
      if (Number.isFinite(rmsDb) && (!this._activeDetail || rmsDb > (this._activeDetail.rmsDb ?? -Infinity))) {
        this._activeDetail = this._makeDetail(rms, rmsDb, noiseFloorDb, startDb, stopDb);
      }
      if (Number.isFinite(noiseFloorDb)) {
        if (!Number.isFinite(this._activeNoiseFloorDb)) {
          this._activeNoiseFloorDb = noiseFloorDb;
        } else {
          this._activeNoiseFloorDb = Math.min(this._activeNoiseFloorDb, noiseFloorDb);
        }
      }

      if (rms < stopR) {
        if (!this._belowSince) this._belowSince = now;
        if (now - this._belowSince >= (this.opts.minSilenceMs ?? 0)) {
          this._recording = false;
          this._aboveSince = 0;
          this._cooldownUntil = now + Math.max(0, this.opts.cooldownMs || 0);

          const duration = Math.max(0, now - (this._speechStartedAt || now));
          const active = this._activeDetail || this._makeDetail(rms, rmsDb, noiseFloorDb, startDb, stopDb);
          const noiseRef = Number.isFinite(this._activeNoiseFloorDb) ? this._activeNoiseFloorDb : noiseFloorDb;
          const peakDb = Number.isFinite(active?.rmsDb) ? active.rmsDb : rmsDb;
          const snrDb = (Number.isFinite(peakDb) && Number.isFinite(noiseRef)) ? peakDb - noiseRef : active?.snrDb;
          const detail = {
            ...active,
            noiseFloorDb: noiseRef,
            snrDb,
            peakDb,
            speechDurationMs: duration,
          };

          try {
            this.cbs.onSpeechEnd && this.cbs.onSpeechEnd(detail);
          } catch {}

          this._activeDetail = null;
          this._activeNoiseFloorDb = null;
          this._speechStartedAt = 0;
        }
      } else {
        this._belowSince = 0;
      }
    }
  }
}

