
import { ensureCSRF } from './csrf.js';

(function(){
  let rec = null;
  let inflight = false;
  let queue = [];
  let chunkSeq = 0;
  let currentUserMsgId = null;

  async function postChunk(sid, csrf, blob, seq, userMsgId){
    const buf = await blob.arrayBuffer();
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    const payload = {
      sid, audio_b64: b64, chunk_seq: seq, user_msg_id: userMsgId
    };
    const res = await fetch('/api/v1/voice/chunk', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf
      },
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
      .finally(() => {
        inflight = false;
        pump(sid, csrf);
      });
  }

  async function startTimesliceIfEnabled(stream, sid, csrf){
    try{
      await ensureCSRF();
      if(rec) return;
      if(!window.MediaRecorder) throw new Error("MediaRecorder unsupported");
      currentUserMsgId = `${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
      rec = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 });
      rec.ondataavailable = ev => {
        if(ev.data && ev.data.size > 0){
          queue.push([ev.data, ++chunkSeq, currentUserMsgId]);
          pump(sid, csrf);
        }
      };
      // Target 96 ms timeslices (within 64–128 ms requirement)
      rec.start(96);
      window.ASKCHIP_STREAMING_TIMESLICE = (sid2, csrf2) => startTimesliceIfEnabled(stream, sid2, csrf2);
    }catch(e){
      console.warn("timeslice failed", e);
    }
  }

  async function fetchSttMode(){
    try{
      const r = await fetch('/api/v1/diag', { credentials: 'include' });
      const j = await r.json();
      return j.stt_mode || 'streaming';
    }catch(e){ return 'streaming'; }
  }

  window.ASKCHIP_FETCH_STT_MODE = fetchSttMode;
  window.ASKCHIP_START_TIMESLICE_IF_ENABLED = startTimesliceIfEnabled;
})();
