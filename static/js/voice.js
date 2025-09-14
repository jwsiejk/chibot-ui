// static/js/voice.js
import { ensureCSRF } from './csrf.js';

let rec = null;
let inflight = false;
let queue = [];
let chunkSeq = 0;
let currentUserMsgId = null;
let visemeCb = () => {};

export function setVisemeCallback(cb){
  visemeCb = (typeof cb === 'function') ? cb : () => {};
}

async function postChunk(sid, csrf, blob, seq, userMsgId){
  const buf = await blob.arrayBuffer();
  const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
  const payload = { sid, audio_b64: b64, chunk_seq: seq, user_msg_id: userMsgId };
  const res = await fetch('/api/v1/voice/chunk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify(payload),
    credentials: 'include'
  });
  if(!res.ok){
    const t = await res.text().catch(()=> '');
    console.warn('voice/chunk failed', res.status, t);
  }
}

function pump(sid, csrf){
  if(inflight || queue.length === 0) return;
  inflight = true;
  const [blob, seq, userMsgId] = queue.shift();
  postChunk(sid, csrf, blob, seq, userMsgId)
    .catch(err => console.warn('postChunk error', err))
    .finally(() => { inflight = false; pump(sid, csrf); });
}

export async function armVAD(stream, opts = {}){
  const sid = (opts && opts.sid) || 'default';
  let csrf = (opts && opts.csrf) || null;
  if(!csrf){
    await ensureCSRF();
    try{
      const r = await fetch('/api/v1/csrf', { credentials: 'include' });
      csrf = r.headers.get('X-CSRF-Token') || '';
    }catch(_){}
  }
  if(!window.MediaRecorder) throw new Error('MediaRecorder unsupported');
  if(rec) return; // already armed
  currentUserMsgId = `${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  rec = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 });
  rec.ondataavailable = ev => {
    if(ev.data && ev.data.size > 0){
      queue.push([ev.data, ++chunkSeq, currentUserMsgId]);
      pump(sid, csrf);
    }
  };
  // 96 ms cadence (inside 64–128 ms acceptance window)
  rec.start(96);
}

export function disarmVAD(){
  try{ if(rec) rec.stop(); }catch(_){}
  rec = null; inflight = false; queue = []; chunkSeq = 0; currentUserMsgId = null;
}

export async function fetchSttMode(){
  try{
    const r = await fetch('/api/v1/diag', { credentials: 'include' });
    const j = await r.json();
    return j.stt_mode || 'streaming';
  }catch(_){ return 'streaming'; }
}
