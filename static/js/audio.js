// static/js/audio.js — unified playback (MSE chunked by MIME) + unlockAudio
import { ChunkedAudioPlayer } from './audio_player.js';
let _player = null;
let _el = null;

export async function unlockAudio(){
  try{
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
  }catch(_){}
}

function ensureEl(){
  if (_el) return _el;
  _el = new Audio();
  _el.autoplay = true;
  return _el;
}
function ensurePlayer(mime){
  ensureEl();
  if (!_player) _player = new ChunkedAudioPlayer(_el, mime);
  _player.setMime(mime);
  return _player;
}

// Accepts base64 strings or Uint8Array chunks; requires MIME
export function playStream(chunks, mime='audio/webm; codecs=opus'){
  try{
    const list = Array.isArray(chunks) ? chunks : [chunks];
    if (!list.length) return;
    const p = ensurePlayer(mime);
    for (const c of list){
      if (!c) continue;
      if (typeof c === 'string') p.appendBase64(c);
      else if (c instanceof Uint8Array) p.appendBytes(c);
      else if (Array.isArray(c)) p.appendBytes(new Uint8Array(c));
    }
  }catch(e){
    console.warn('[audio] playStream error', e);
  }
}

export function stopPlayback(){
  try{ _player?.stop(); }catch{}
}

export function isPlaying(){ try{ return !!(_el && !_el.paused); }catch{return false;} }
export function setVisemeCallback(_fn){ /* no-op in 2D */ }
