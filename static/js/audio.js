// static/js/audio.js — robust MP3 playback + unlockAudio helper
let _audio = null;
let _ctx = null;

export async function unlockAudio(){
  try{
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    _ctx = _ctx || new AC();
    if (_ctx.state === 'suspended') await _ctx.resume();
    const o = _ctx.createOscillator();
    const g = _ctx.createGain();
    g.gain.value = 0.00001;
    o.connect(g).connect(_ctx.destination);
    o.start();
    o.stop(_ctx.currentTime + 0.01);
  }catch(e){ /* ignore */ }
}

// Accepts an array of Uint8Array MP3 chunks (or a single chunk).
export function playStream(chunks){
  try{
    const list = Array.isArray(chunks) ? chunks : [chunks];
    if (!list.length) return;
    const total = list.reduce((n, c)=> n + (c?.length || 0), 0);
    if (!total) return;
    const buf = new Uint8Array(total);
    let off = 0;
    for (const c of list){
      if (!c || !c.length) continue;
      buf.set(c, off);
      off += c.length;
    }
    const blob = new Blob([buf], { type: 'audio/mpeg' });
    if (_audio){ try{ _audio.pause(); }catch{} }
    _audio = new Audio(URL.createObjectURL(blob));
    _audio.addEventListener('ended', ()=> {
      try{ window.dispatchEvent(new CustomEvent('chip:tts-ended')); }catch{}
    });
    _audio.play().catch(()=>{});
  }catch(e){
    console.warn('[audio] playStream error', e);
  }
}

export function stopPlayback(){ try{ _audio?.pause(); }catch{} }
export function isPlaying(){ return !!(_audio && !_audio.paused); }

// no-op; placeholder for alignment callbacks when used
export function setVisemeCallback(fn){ /* not used in 2D mode */ }
