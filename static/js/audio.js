// static/js/audio.js — unified playback (MSE chunked by MIME) + unlockAudio
// Backwards compatible:
//   • playStream(chunks, mime?)            // legacy
//   • playStream(frameObject)              // { mime, audio_chunks:[], is_last }
// New helpers:
//   • audioEnd() — mark natural end-of-utterance (drain then endOfStream)
//   • audioTeardown() — hard reset pipeline (optional)

import { ChunkedAudioPlayer } from './audio_player.js';
import { logIfEnabled } from './util/logging.js';

const DEFAULT_SIGNATURE = Object.freeze({ rms: 0, rmsDb: -Infinity, mfcc: [], timestamp: 0 });

let _player = null;
let _el = null;
let _lastTtsState = 'ended';

const _analysis = {
  ctx: null,
  source: null,
  analyser: null,
  raf: null,
  timeBuf: null,
  freqBuf: null,
  signature: { ...DEFAULT_SIGNATURE },
  mfccConfig: null,
};

const TTS_IDLE_VOLUME = 1.0;
const TTS_ATTENUATED_VOLUME = 0.5; // ~6 dB reduction to limit mic bleed

const DEFAULT_MIME = 'audio/webm; codecs="opus"';

function _emitTtsState(state) {
  if (!state || state === _lastTtsState) return;
  _lastTtsState = state;
  logIfEnabled(() => {
    try {
      const tsMs = Date.now();
      const sessionId = (() => {
        try { return window?.__askchip_voice_session_id || null; } catch { return null; }
      })();
      const turnId = (() => {
        try { return window?.__askchip_turn_trace_id || null; } catch { return null; }
      })();
      if (state === 'playing') {
        console.info('[tts] playing: true', { ts_ms: tsMs, session_id: sessionId, turn_id: turnId });
      } else if (state === 'ended' || state === 'stopped') {
        console.info('[tts] playing: false', { ts_ms: tsMs, session_id: sessionId, turn_id: turnId });
      }
    } catch {}
  });
  try {
    window.dispatchEvent(new CustomEvent('chip-tts', { detail: { state } }));
  } catch {}
}

export async function unlockAudio() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ctx = new AC();
    if (ctx.state === 'suspended') await ctx.resume();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    g.gain.value = 0.00001;
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.01);
  } catch (_) {}
}

function ensureEl() {
  if (_el) return _el;
  _el = new Audio();
  _el.autoplay = true;
  // _el.playsInline = true; // optional (mobile)
  try {
    _el.volume = TTS_IDLE_VOLUME;
    _el.addEventListener('playing', () => {
      _el.volume = TTS_ATTENUATED_VOLUME;
      _emitTtsState('playing');
    });
    const onEnd = () => {
      _el.volume = TTS_IDLE_VOLUME;
      _emitTtsState('ended');
      _analysis.signature = { ...DEFAULT_SIGNATURE };
    };
    _el.addEventListener('pause', onEnd);
    _el.addEventListener('ended', onEnd);
  } catch {}
  try { _ensureSignatureTracker(); } catch {}
  return _el;
}

function ensurePlayer(mime) {
  ensureEl();
  const m = mime || DEFAULT_MIME;
  if (!_player) _player = new ChunkedAudioPlayer(_el, m);
  _player.setMime(m);
  return _player;
}

function _ensureSignatureTracker() {
  if (!_el) return;
  if (_analysis.ctx && _analysis.analyser) return;

  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;

  try {
    const ctx = new AC();
    if (ctx.state === 'suspended') {
      try { ctx.resume(); } catch {}
    }

    let source = null;
    try {
      if (typeof _el.captureStream === 'function') {
        const stream = _el.captureStream();
        if (stream) {
          source = ctx.createMediaStreamSource(stream);
        }
      }
    } catch {}

    if (!source) {
      try {
        source = ctx.createMediaElementSource(_el);
        const passthrough = ctx.createGain();
        passthrough.gain.value = 1;
        source.connect(passthrough).connect(ctx.destination);
      } catch (err) {
        console.warn('[audio] failed to create analysis source', err);
        try { ctx.close(); } catch {}
        return;
      }
    }

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0;
    source.connect(analyser);

    _analysis.ctx = ctx;
    _analysis.source = source;
    _analysis.analyser = analyser;
    _analysis.timeBuf = new Float32Array(analyser.fftSize);
    _analysis.freqBuf = new Float32Array(analyser.frequencyBinCount);
    _analysis.signature = { ...DEFAULT_SIGNATURE };

    _scheduleSignatureUpdate();
  } catch (e) {
    console.warn('[audio] unable to initialize playback analysis', e);
  }
}

function _teardownSignatureTracker() {
  if (_analysis.raf) {
    const cancel = window.cancelAnimationFrame
      || window.webkitCancelAnimationFrame
      || window.mozCancelAnimationFrame
      || ((id) => clearTimeout(id));
    try { cancel(_analysis.raf); } catch {}
    _analysis.raf = null;
  }

  try { _analysis.source && _analysis.source.disconnect(); } catch {}
  try { _analysis.analyser && _analysis.analyser.disconnect(); } catch {}
  try { _analysis.ctx && _analysis.ctx.close && _analysis.ctx.close(); } catch {}

  _analysis.ctx = null;
  _analysis.source = null;
  _analysis.analyser = null;
  _analysis.timeBuf = null;
  _analysis.freqBuf = null;
  _analysis.mfccConfig = null;
  _analysis.signature = { ...DEFAULT_SIGNATURE };
}

function _scheduleSignatureUpdate() {
  const raf = window.requestAnimationFrame
    || window.webkitRequestAnimationFrame
    || window.mozRequestAnimationFrame
    || ((cb) => setTimeout(() => cb(Date.now()), 33));
  if (typeof raf !== 'function') return;

  const tick = () => {
    try { _updateSignature(); } catch {}
    _analysis.raf = raf(tick);
  };

  if (_analysis.raf) {
    return;
  }
  _analysis.raf = raf(tick);
}

function _hzToMel(hz) {
  return 2595 * Math.log10(1 + hz / 700);
}

function _melToHz(mel) {
  return 700 * (Math.pow(10, mel / 2595) - 1);
}

function _ensureMfccConfig(sampleRate, fftSize) {
  if (_analysis.mfccConfig && _analysis.mfccConfig.sampleRate === sampleRate && _analysis.mfccConfig.fftSize === fftSize) {
    return _analysis.mfccConfig;
  }

  const filterCount = 20;
  const mfccCount = 13;
  const minMel = _hzToMel(20);
  const maxMel = _hzToMel(sampleRate / 2);
  const melStep = (maxMel - minMel) / (filterCount + 1);
  const filters = [];
  for (let i = 0; i < filterCount; i++) {
    const melStart = minMel + melStep * i;
    const melCenter = minMel + melStep * (i + 1);
    const melEnd = minMel + melStep * (i + 2);
    const start = Math.floor((_melToHz(melStart) / sampleRate) * fftSize);
    const center = Math.floor((_melToHz(melCenter) / sampleRate) * fftSize);
    const end = Math.floor((_melToHz(melEnd) / sampleRate) * fftSize);
    filters.push({ start: Math.max(1, start), center: Math.max(1, center), end: Math.max(1, end) });
  }

  _analysis.mfccConfig = { sampleRate, fftSize, filterCount, mfccCount, filters };
  return _analysis.mfccConfig;
}

function _computeMfcc(freqBuf, sampleRate, fftSize) {
  if (!freqBuf) return [];
  const cfg = _ensureMfccConfig(sampleRate, fftSize);
  const energies = new Array(cfg.filterCount).fill(0);

  for (let i = 0; i < cfg.filterCount; i++) {
    const { start, center, end } = cfg.filters[i];
    let energy = 0;
    for (let bin = start; bin < end && bin < freqBuf.length; bin++) {
      const leftWidth = Math.max(center - start, 1);
      const rightWidth = Math.max(end - center, 1);
      let weight;
      if (bin <= center) {
        weight = (bin - start) / leftWidth;
      } else {
        weight = (end - bin) / rightWidth;
      }
      weight = Math.max(0, Math.min(1, weight));
      const magnitude = Math.pow(10, freqBuf[bin] / 20);
      const power = magnitude * magnitude;
      energy += power * weight;
    }
    energies[i] = Math.log(Math.max(1e-12, energy));
  }

  const coeffs = [];
  for (let k = 0; k < cfg.mfccCount; k++) {
    let sum = 0;
    for (let n = 0; n < energies.length; n++) {
      sum += energies[n] * Math.cos(Math.PI * k * (n + 0.5) / energies.length);
    }
    const scale = k === 0 ? Math.sqrt(1 / energies.length) : Math.sqrt(2 / energies.length);
    coeffs.push(sum * scale);
  }
  return coeffs;
}

function _updateSignature() {
  if (!_analysis.analyser || !_analysis.timeBuf) {
    _analysis.signature = { ...DEFAULT_SIGNATURE };
    return;
  }

  try {
    const analyser = _analysis.analyser;
    const timeBuf = _analysis.timeBuf;
    const freqBuf = _analysis.freqBuf;
    analyser.getFloatTimeDomainData(timeBuf);

    let sum = 0;
    for (let i = 0; i < timeBuf.length; i++) {
      const v = timeBuf[i];
      sum += v * v;
    }
    const rms = Math.sqrt(sum / timeBuf.length);
    const rmsDb = 20 * Math.log10(Math.max(1e-8, rms));

    if (freqBuf) {
      analyser.getFloatFrequencyData(freqBuf);
    }

    const sampleRate = _analysis.ctx?.sampleRate || 48000;
    const mfcc = freqBuf ? _computeMfcc(freqBuf, sampleRate, analyser.fftSize) : [];

    _analysis.signature = {
      rms,
      rmsDb,
      mfcc,
      timestamp: (typeof performance !== 'undefined' && performance?.now) ? performance.now() : Date.now(),
    };
  } catch {
    _analysis.signature = { ...DEFAULT_SIGNATURE };
  }
}

export function getPlaybackSignature() {
  const sig = _analysis.signature || DEFAULT_SIGNATURE;
  return {
    rms: sig.rms ?? 0,
    rmsDb: Number.isFinite(sig.rmsDb) ? sig.rmsDb : DEFAULT_SIGNATURE.rmsDb,
    mfcc: Array.isArray(sig.mfcc) ? sig.mfcc.slice() : [],
    timestamp: sig.timestamp ?? 0,
  };
}

// Accepts WS frame ({mime, audio_chunks, is_last}) OR legacy (chunks, mime?)
export function playStream(frameOrChunks, maybeMime) {
  try {
    _ensureSignatureTracker();
    // --- New frame shape ----------------------------------------------------
    if (frameOrChunks && typeof frameOrChunks === 'object' && !Array.isArray(frameOrChunks)) {
      const frame = frameOrChunks;
      const mime = frame.mime || maybeMime || DEFAULT_MIME;
      const p = ensurePlayer(mime);
      const list = Array.isArray(frame.audio_chunks) ? frame.audio_chunks : [];
      for (const c of list) {
        if (!c) continue;
        if (typeof c === 'string') p.appendBase64(c);
        else if (c instanceof Uint8Array) p.appendBytes(c);
        else if (Array.isArray(c)) p.appendBytes(new Uint8Array(c));
      }
      if (frame.is_last) p.end();
      return;
    }

    // --- Legacy call signature ----------------------------------------------
    const list = Array.isArray(frameOrChunks) ? frameOrChunks : [frameOrChunks];
    if (!list.length) return;
    const mime = maybeMime || DEFAULT_MIME;
    const p = ensurePlayer(mime);
    for (const c of list) {
      if (!c) continue;
      if (typeof c === 'string') p.appendBase64(c);
      else if (c instanceof Uint8Array) p.appendBytes(c);
      else if (Array.isArray(c)) p.appendBytes(new Uint8Array(c));
    }
  } catch (e) {
    console.warn('[audio] playStream error', e);
  }
}

export function audioEnd() {
  try { _player?.end(); } catch {}
}

export function audioTeardown() {
  try { _player?.teardown(); } catch {}
  try { _teardownSignatureTracker(); } catch {}
}

export function stopPlayback() {
  try { _player?.stop(); } catch {}
  try { ensureEl().pause(); } catch {}
  try { ensureEl().volume = TTS_IDLE_VOLUME; } catch {}
  _emitTtsState('ended');
}

export function isPlaying() {
  try { return !!(_el && !_el.paused); } catch { return false; }
}

export function pausePlayback() {
  try {
    const el = ensureEl();
    if (!el.paused) {
      el.pause();
      el.volume = TTS_IDLE_VOLUME;
      _emitTtsState('ended');
    }
  } catch {}
}

export function resumePlayback() {
  try {
    const el = ensureEl();
    if (el.paused) {
      const rv = el.play();
      if (rv && typeof rv.then === 'function') {
        rv.then(() => _emitTtsState('playing')).catch(() => {});
      } else {
        _emitTtsState('playing');
      }
    }
  } catch {}
}

export function setVisemeCallback(_fn) { /* no-op in 2D */ }
