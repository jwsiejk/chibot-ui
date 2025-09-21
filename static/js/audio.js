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

const DEFAULT_MIME = 'audio/webm; codecs="opus"';

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
}

export function isPlaying() {
  try { return !!(_el && !_el.paused); } catch { return false; }
}

export function setVisemeCallback(_fn) { /* no-op in 2D */ }
