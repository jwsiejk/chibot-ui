// static/js/audio.js — unified playback (MSE chunked by MIME) + unlockAudio
// Backwards compatible:
//   • playStream(chunks, mime?)            // legacy
//   • playStream(frameObject)              // { mime, audio_chunks:[], is_last }
// New helpers:
//   • audioEnd() — mark natural end-of-utterance (drain then endOfStream)
//   • audioTeardown() — hard reset pipeline (optional)

import { ChunkedAudioPlayer } from './audio_player.js';

let _player = null;
let _el = null;
let _lastTtsState = 'ended';

const TTS_IDLE_VOLUME = 1.0;
const TTS_ATTENUATED_VOLUME = 0.5; // ~6 dB reduction to limit mic bleed

const DEFAULT_MIME = 'audio/webm; codecs="opus"';

function _emitTtsState(state) {
  if (!state || state === _lastTtsState) return;
  _lastTtsState = state;
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
    };
    _el.addEventListener('pause', onEnd);
    _el.addEventListener('ended', onEnd);
  } catch {}
  return _el;
}

function ensurePlayer(mime) {
  ensureEl();
  const m = mime || DEFAULT_MIME;
  if (!_player) _player = new ChunkedAudioPlayer(_el, m);
  _player.setMime(m);
  return _player;
}

// Accepts WS frame ({mime, audio_chunks, is_last}) OR legacy (chunks, mime?)
export function playStream(frameOrChunks, maybeMime) {
  try {
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
