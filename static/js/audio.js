import { setState, STATES } from "./state.js";

let audioCtx;
let playing = false;
let onVisemes = null; // callback to sync visemes

export function setVisemeCallback(cb){ onVisemes = cb; }

export async function playStream(chunks, visemes){
  // chunks: ArrayBuffer[] of audio data (stubbed); visemes: [{t:ms, v:id}, ...]
  ensureCtx();
  playing = true;
  setState(STATES.RESPONDING);
  // Stub: just simulate playback timing using visemes or chunks length
  const total = Math.max(500, (visemes?.at(-1)?.t || (chunks?.length||1)*250));
  if (Array.isArray(visemes)){
    scheduleVisemes(visemes);
  }
  await new Promise(r => setTimeout(r, total));
  playing = false;
}

export function stopPlayback(){
  // Stub: would stop sources, etc.
  playing = false;
}

export function isPlaying(){ return playing; }

function ensureCtx(){
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AC();
  }
}

function scheduleVisemes(ves){
  if (!onVisemes) return;
  const start = performance.now();
  ves.forEach(v => {
    const due = v.t;
    setTimeout(() => { onVisemes(v); }, Math.max(0, due - (performance.now()-start)));
  });
}