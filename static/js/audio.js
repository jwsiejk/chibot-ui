
import { setState, STATES } from "./state.js";

let audioEl = null;
let playing = false;
let onVisemes = null;

export function setVisemeCallback(cb){ onVisemes = cb; }

export async function playStream(chunks, visemes){
  stopPlayback();
  const blob = new Blob(chunks.map(b => new Uint8Array(b)), { type: "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  audioEl = new Audio();
  audioEl.src = url;
  playing = true;
  setState(STATES.RESPONDING);
  const start = performance.now();
  if (Array.isArray(visemes)){
    visemes.forEach(v => {
      const t = v.t ?? v.t_ms ?? 0;
      setTimeout(() => { if (onVisemes && playing) onVisemes(v); }, Math.max(0, t - (performance.now() - start)));
    });
  }
  audioEl.onended = () => { playing = false; URL.revokeObjectURL(url); };
  try { await audioEl.play(); } catch(e){ playing = false; }
}

export function stopPlayback(){
  try {
    if (audioEl) { audioEl.pause(); audioEl.src=""; }
  } catch(e){}
  playing = false;
}

export function isPlaying(){ return playing; }
