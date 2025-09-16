// static/js/voice.js — MediaRecorder 96ms timeslices → POST /api/v1/voice/chunk
import { ensureCSRF } from './csrf.js';
import { getSID } from './util/sid.js';

export let currentStream = null;
let rec = null;
let queue = [];
let inflight = false;
let chunkSeq = 0;
let currentUserMsgId = null;

// NEW: drop the very first tiny blob some browsers emit
let firstBlobSeen = false;

export async function initMic(){
  if (currentStream && currentStream.active) return currentStream;
  currentStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      sampleRate: 48000,
      echoCancellation: true,
      noiseSuppression: true
    },
    video: false
  });
  return currentStream;
}

export function getCurrentStream(){ return currentStream; }

function b64(bytes){
  let bin=''; const len=bytes.length;
  for(let i=0;i<len;i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

async function sendLoop(){
  if (inflight) return;
  inflight = true;
  try{
    while(queue.length){
      const bytes = queue.shift();
      const payload = {
        sid: getSID(),
        user_msg_id: currentUserMsgId,
        chunk_seq: chunkSeq++,
        audio_b64: b64(bytes)
      };
      const headers = new Headers({ 'Content-Type':'application/json' });
      const csrf = await ensureCSRF().catch(()=> '');
      if (csrf) headers.set('X-CSRF-Token', csrf);

      const res = await fetch('/api/v1/voice/chunk', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        credentials: 'include'
      });
      if (!res.ok) {
        console.warn('[voice] chunk POST failed', res.status);
        break; // avoid tight loop on persistent errors
      }
    }
  } finally { inflight = false; }
}

export async function armVAD(stream, opts={}){
  if (!stream) throw new Error('armVAD requires a MediaStream');
  if (rec && rec.state !== 'inactive') return;    // already running

  chunkSeq = 0;
  currentUserMsgId = opts.userMsgId || (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
  firstBlobSeen = false;

  // Prefer containerized WebM/Opus (Deepgram reads header automatically)
  const mimeOptions = [
    'audio/webm;codecs=opus',
    'audio/webm;codecs=opus,pcm',
    'audio/webm'
  ];
  let mime = '';
  for (const m of mimeOptions){
    if (MediaRecorder.isTypeSupported(m)){ mime = m; break; }
  }

  try {
    rec = new MediaRecorder(stream, { mimeType: mime || undefined, audioBitsPerSecond: 128000 });
  } catch {
    rec = new MediaRecorder(stream); // last resort
  }

  rec.ondataavailable = async (ev) => {
    try{
      if (!ev.data || ev.data.size === 0) return;

      // Skip the very first tiny blob so DG sees a valid container header first
      if (!firstBlobSeen) {
        firstBlobSeen = true;
        if (ev.data.size < 128) return;
      }

      const buf = new Uint8Array(await ev.data.arrayBuffer());
      queue.push(buf);
      if (!inflight) sendLoop();
    }catch(e){ console.warn('[voice] dataavailable error', e); }
  };
  rec.onerror = (e) => console.warn('[voice] recorder error', e);
  rec.onstop = ()=>{};

  // 96 ms slices (server accepts 64–128 ms)
  try { rec.start(96); } catch { rec.start(); }
}

export function disarmVAD(){
  try{ if (rec && rec.state !== 'inactive') rec.stop(); }catch{}
  rec = null;
  inflight = false;
  queue = [];
  chunkSeq = 0;
  currentUserMsgId = null;
  firstBlobSeen = false;
}

// kept for API parity; not exposed to UI in this build
export function setVadBoost(_v){}
