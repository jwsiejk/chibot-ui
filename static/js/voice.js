
import { ensureCSRF } from './csrf.js';

let mediaStream = null;
let ctx = null;
let analyser = null;
let rec = null;
let chunks = [];
let vadOn = false;
let silenceMs = 0;
let speechMs = 0;

// Adaptive baseline + boost for echo-aware barge-in
let vadBoost = 1.0;
let vadBase = 0.025;   // will auto-calibrate
let vadCalibrating = false;

// Init mic + analyser
export async function initMic(){
  try{
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      video: false
    });
  }catch(e){
    console.warn('getUserMedia error', e);
    throw e;
  }
  try{
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(mediaStream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    src.connect(analyser);
  }catch(e){
    console.warn('AudioContext error', e);
    throw e;
  }
  return mediaStream;
}

// HUD event (optional)
function emitHud(level, thr){
  try{
    window.dispatchEvent(new CustomEvent('chip:vad', { detail: { level, thr, speechMs, silenceMs, armed: vadOn, boost: vadBoost } }));
  }catch(e){}
}

function instantLevel(){
  if(!analyser) return 0.0;
  const b = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(b);
  let s = 0;
  for(let i=0; i<b.length; i++){
    const v = (b[i] - 128) / 128;
    s += v*v;
  }
  return Math.sqrt(s / b.length);
}

export function setVadBoost(mult){
  const m = Number(mult);
  if(!Number.isFinite(m)) return;
  vadBoost = Math.max(1.0, m);
}

// VAD loop
async function loop(){
  if(!vadOn) return;
  try{ await ctx.resume(); }catch(e){}
  const level = instantLevel();
  const thr = (vadBase || 0.025) * vadBoost;
  emitHud(level, thr);
  const ms = 60;
  if(level > thr) speechMs += ms; else silenceMs += ms;
  if(speechMs >= 300 && silenceMs >= 400){
    vadOn = false;
    try{ if(rec && rec.state !== 'inactive') rec.stop(); }catch(e){}
    return;
  }
  setTimeout(loop, ms);
}

// Arm VAD (idempotent) with quick baseline calibration
export function armVAD(){
  if(!mediaStream) return;
  if(vadOn) return;
  vadOn = true;
  // Calibrate baseline ~ 800ms
  vadCalibrating = true;
  let acc = 0, n = 0;
  const endAt = Date.now() + 800;
  (function calib(){
    try{
      const l = instantLevel();
      acc += l; n += 1;
    }catch(e){}
    if(Date.now() < endAt){ return setTimeout(calib, 40); }
    const avg = n ? acc / n : 0.02;
    vadBase = Math.min(Math.max(avg * 1.8, 0.01), 0.05); // clamp
    vadCalibrating = false;
  })();
  try{ if(rec && rec.state !== 'inactive') rec.stop(); }catch(e){}
  try{
    rec = new MediaRecorder(mediaStream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 });
    rec.ondataavailable = (e)=>{ try{ if(e.data && e.data.size) chunks.push(e.data); }catch(ex){} };
    rec.onstop = ()=>{ try{ postSTT(); }catch(ex){} };
    rec.start();
    chunks = [];
    silenceMs = 0;
    speechMs = 0;
    loop();
  }catch(e){
    console.warn('MediaRecorder error', e);
    vadOn = false;
  }
}

export function disarmVAD(){
  vadOn = false;
  try{ if(rec && rec.state !== 'inactive') rec.stop(); }catch(e){}
  try{ chunks = []; silenceMs = 0; speechMs = 0; }catch(e){}
}

// Send blob to STT
async function postSTT(){
  try{
    const sid = localStorage.getItem('chip.sid') || '';
    const fd = new FormData();
    const blob = new Blob(chunks, { type: 'audio/webm' });
    fd.append('file', blob, 'turn.webm');
    fd.append('meta', JSON.stringify({
      language: 'en',
      avg_rms: 0, max_rms: 0, // placeholder; optional client metrics
    }));
    const headers = { 'X-CSRF-Token': await ensureCSRF() };
    await fetch(`/api/v1/voice/stt?session_id=${encodeURIComponent(sid)}`, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: fd
    });
  }catch(e){
    console.warn('STT error', e);
  }
}
