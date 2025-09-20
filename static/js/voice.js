
// static/js/voice.js — Phase 4 WS-only mic sender + controls
import { sendAudioChunk, sendCloseStream, configure, bufferedAmount, waitWSOpen, openWS } from './ws.js';

export let currentStream = null;
let rec = null;
let backoffTimer = null;
let chunkSeq = 0;
let currentUserMsgId = null;
let firstBlobSeen = false;

export async function initMic(){
  if (currentStream && currentStream.active) return currentStream;
  currentStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
      sampleRate: 48000,
      sampleSize: 16,
    }
  });
  return currentStream;
}

function _pauseRecorder(){
  try{ if (rec && rec.state === 'recording') rec.pause(); }catch{}
}
function _resumeRecorder(){
  try{ if (rec && rec.state === 'paused') rec.resume(); }catch{}
}

export async function armVAD(stream, opts={}){
  if (!stream) throw new Error('armVAD requires a MediaStream');
  if (rec && rec.state !== 'inactive') return;    // already running

  // Ensure WS is open before starting
  openWS();
  await waitWSOpen();

  chunkSeq = 0;
  currentUserMsgId = opts.userMsgId || (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
  firstBlobSeen = false;

  // Prefer WebM/Opus
  const mimeOptions = [
    'audio/webm;codecs=opus',
    'audio/webm;codecs=opus,pcm',
    'audio/webm'
  ];
  let mime = '';
  for (const m of mimeOptions){
    if (MediaRecorder.isTypeSupported(m)){ mime = m; break; }
  }
  if (!mime) mime = 'audio/webm';

  rec = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 128000 });

  rec.ondataavailable = async (ev) => {
    const blob = ev.data;
    if (!blob || !blob.size) return;

    // Some browsers emit a tiny primer chunk; drop only if truly tiny (<32 bytes)
    if (!firstBlobSeen){
      if (blob.size < 32){
        // drop primer
        return;
      }
      firstBlobSeen = true;
    }

    // Backpressure: if bufferedAmount too large, pause until it drains
    const HIGH_WATER = 1.5 * 1024 * 1024; // 1.5 MB
    if (bufferedAmount() > HIGH_WATER){
      _pauseRecorder();
      if (backoffTimer) clearInterval(backoffTimer);
      backoffTimer = setInterval(()=>{
        if (bufferedAmount() < 256 * 1024){ // 256 KB
          clearInterval(backoffTimer); backoffTimer = null;
          _resumeRecorder();
        }
      }, 100);
    }

    try{
      await sendAudioChunk(blob);
      chunkSeq++;
    }catch(e){
      console.warn('[voice] failed to send chunk', e);
    }
  };
  rec.onerror = (e) => console.warn('[voice] recorder error', e);
  rec.onstop = ()=>{};

  // Kick off recording with ~150–200 ms slices
  try { rec.start(150); } catch { rec.start(); }

  // Send a Configure frame at the start of the turn (optional; placeholder for future tuning)
  configure({ type: "Configure", vad: "client", media: "webm_opus", userMsgId: currentUserMsgId });
}

export function disarmVAD(){
  try{ if (rec && rec.state !== 'inactive') rec.stop(); }catch{}
  rec = null;
  if (backoffTimer){ try{ clearInterval(backoffTimer); }catch{} backoffTimer = null; }
  chunkSeq = 0;
  currentUserMsgId = null;
  firstBlobSeen = false;
}

export function bargeIn(){
  try { sendCloseStream(); } catch {}
  // TTS should be paused by the UI’s player; app.js can listen to this and stop playback
}

export function setVadBoost(_v){} // retained for API parity
