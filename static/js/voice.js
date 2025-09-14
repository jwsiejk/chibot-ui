// static/js/voice.js
//
// Exports expected by your ws/app code:
//   - initMic(): Promise<MediaStream>
//   - armVAD(stream, opts?): start 96 ms timeslicing → POST /api/v1/voice/chunk
//   - disarmVAD(): stop recorder & clear queues
//   - setVadBoost(val:number): no-op placeholder to keep parity with callers
//   - currentStream: last opened MediaStream
//
// Notes:
//  • Uses X-CSRF-Token header (server issues via /api/v1/csrf or other GETs).
//  • Sends { sid, user_msg_id, chunk_seq, audio_b64 } to /api/v1/voice/chunk.
//  • 96 ms slices (within 64–128 ms acceptance window).
//  • No dependencies on legacy /voice/stt/stream.
//

import { ensureCSRF } from './csrf.js';

export let currentStream = null;

let rec = null;
let inflight = false;
let queue = [];
let chunkSeq = 0;
let currentUserMsgId = null;
let vadBoost = 1.0;

/** Optional parity hook used by older code paths */
export function setVadBoost(val){
  try {
    const n = Number(val);
    if (!Number.isNaN(n) && n > 0) vadBoost = n;
  } catch (_) {}
}

/** Request microphone and return a MediaStream (echoCancellation on) */
export async function initMic() {
  if (currentStream && currentStream.active) return currentStream;
  const constraints = {
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
      sampleRate: 48000
    }
  };
  currentStream = await navigator.mediaDevices.getUserMedia(constraints);
  return currentStream;
}

async function postChunk(sid, csrf, blob, seq, userMsgId){
  const buf = await blob.arrayBuffer();
  const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
  const payload = { sid, audio_b64: b64, chunk_seq: seq, user_msg_id: userMsgId };
  const res = await fetch('/api/v1/voice/chunk', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrf
    },
    body: JSON.stringify(payload),
    credentials: 'include'
  });
  if (!res.ok){
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
    .finally(() => {
      inflight = false;
      pump(sid, csrf);
    });
}

/**
 * Start 96 ms timeslicing on an existing MediaStream.
 * opts: { sid?: string, csrf?: string }
 */
export async function armVAD(stream, opts = {}){
  if (!stream) throw new Error('armVAD requires a MediaStream');
  if (rec) return; // already armed

  const sid = (opts && opts.sid) || 'default';
  let csrf = (opts && opts.csrf) || '';

  // Ensure CSRF token
  try {
    await ensureCSRF();
    if (!csrf) {
      const r = await fetch('/api/v1/csrf', { credentials: 'include' });
      csrf = r.headers.get('X-CSRF-Token') || '';
    }
  } catch (_) {}

  currentUserMsgId = `${Date.now()}-${Math.random().toString(36).slice(2,8)}`;

  rec = new MediaRecorder(stream, {
    mimeType: 'audio/webm;codecs=opus',
    audioBitsPerSecond: 128000
  });

  rec.ondataavailable = ev => {
    if (ev.data && ev.data.size > 0) {
      // (Optional) could use vadBoost to gate push, but we just keep parity API
      queue.push([ev.data, ++chunkSeq, currentUserMsgId]);
      pump(sid, csrf);
    }
  };

  // Target 96 ms (within 64–128 ms requirement)
  rec.start(96);
}

/** Stop timeslicing and reset internal state */
export function disarmVAD(){
  try { if(rec) rec.stop(); } catch(_) {}
  rec = null;
  inflight = false;
  queue = [];
  chunkSeq = 0;
  currentUserMsgId = null;
}

/** Utility the UI sometimes calls to check server STT mode (kept for parity) */
export async function fetchSttMode(){
  try{
    const r = await fetch('/api/v1/diag', { credentials: 'include' });
    const j = await r.json();
    return j.stt_mode || 'streaming';
  }catch(_){ return 'streaming'; }
}
