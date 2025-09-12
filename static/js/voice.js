import { ensureCSRF } from './csrf.js';

let mediaStream = null;
let ctx = null;
let analyser = null;
// NOTE: batch-recorder removed (no batch STT)
let vadOn = false;
let silenceMs = 0;
let speechMs = 0;

// Adaptive baseline + boost for echo-aware barge-in
let vadBoost = 1.0;
let vadBase = 0.025;   // will auto-calibrate
let vadCalibrating = false;

// === Public helper to expose the active stream ===
export function currentStream(){
  return mediaStream || null;
}

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

// VAD loop (no batch recording — just level tracking for barge-in UX)
async function loop(){
  if(!vadOn) return;
  try{ await ctx?.resume(); }catch(e){}
  const level = instantLevel();
  const thr = (vadBase || 0.025) * vadBoost;
  emitHud(level, thr);
  const ms = 60;
  if(level > thr) speechMs += ms; else silenceMs += ms;

  // basic stop condition to avoid running forever
  if(speechMs >= 300 && silenceMs >= 400){
    vadOn = false;
    return;
  }
  setTimeout(loop, ms);
}

// Arm VAD (idempotent) with quick baseline calibration — NO batch upload anymore
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

  // Reset counters and start loop (no MediaRecorder here)
  try{ silenceMs = 0; speechMs = 0; }catch(e){}
  loop();
}

export function disarmVAD(){
  vadOn = false;
  try{ silenceMs = 0; speechMs = 0; }catch(e){}
}

// === (Removed) Batch STT upload path ===
// The old postSTT() + recorder-onstop path has been removed so the UI
// does not call /api/v1/voice/stt (batch) anymore.

// === Streaming timeslice sender (Option B Phase 3) ===
// Flagged by stt_mode returned by the server; only starts once.
(function(){
  let _sttMode = "batch";
  let _streamEnabled = false;

  async function fetchSttMode(){
    try{
      const r = await fetch("/api/v1/voice/stt-mode", { credentials: "include" });
      if(r.ok){
        const j = await r.json();
        _sttMode = j.stt_mode || "batch";
      }
    }catch(e){}
  }

  async function startTimesliceIfEnabled(stream, sessionId, csrfToken){
    if(_sttMode !== "stream" || _streamEnabled) return;
    _streamEnabled = true;
    try{
      const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const queue = [];
      let inflight = false;

      async function pump(){
        if(inflight || queue.length === 0) return;
        inflight = true;
        const blob = queue.shift();
        try{
          const qs = sessionId ? ("?session_id="+encodeURIComponent(sessionId)) : "";
          let ok = true;
          try{
            const rr = await fetch("/api/v1/voice/stt/stream"+qs, {
              method: "POST",
              headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
              body: blob
            });
            ok = rr.ok;
          }catch(e){ ok = false; }
          if(!ok){
            window.ASKCHIP_STREAM_FAILS = (window.ASKCHIP_STREAM_FAILS||0)+1;
            if(window.ASKCHIP_STREAM_FAILS >= 10){
              console.warn("stt stream disabled due to repeated failures");
              // stop producing further network load until reload
              queue.length = 0;
              inflight = false;
              return;
            }
          }
        } finally {
          inflight = false;
          pump();
        }
      }

      rec.addEventListener("dataavailable", (ev) => {
        if(ev.data && ev.data.size > 0){
          if(queue.length >= 8) queue.shift();
          queue.push(ev.data);
          pump();
        }
      });
      rec.start(250);

      // expose a hook so other modules can trigger this with the current stream
      window.ASKCHIP_STREAMING_TIMESLICE = (sid, csrf) => startTimesliceIfEnabled(stream, sid, csrf);
    }catch(e){ console.warn("timeslice failed", e); }
  }

  // Public bootstrap to be called by code that owns the MediaStream.
  window.ASKCHIP_FETCH_STT_MODE = fetchSttMode;
  window.ASKCHIP_START_TIMESLICE_IF_ENABLED = startTimesliceIfEnabled;
})();
