// static/js/voice.js — WS mic capture + streaming (production-safe)
import { sendAudioChunk, sendCloseStream, bufferedAmount, waitWSOpen, openWS } from './ws.js';

export let currentStream = null;
let rec = null;
let backoffTimer = null;
let firstBlobSeen = false;

/** Get or request microphone stream (48k mono, echo/noise controls). */
export async function initMic(){
  if (currentStream && currentStream.active) return currentStream;
  currentStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
      sampleRate: 48000
    }
  });
  return currentStream;
}

/** Start MediaRecorder and send 200ms Opus/WebM chunks over the WS. */
export async function armVAD(stream){
  await openWS(); await waitWSOpen();
  if (!stream) stream = await initMic();

  const mimeCandidates = [
    'audio/webm;codecs=opus',
    'audio/webm;codecs=opus;rate=48000',
    'audio/webm'
  ];
  let mime = mimeCandidates.find(MediaRecorder.isTypeSupported) || '';

  try { rec?.stop(); } catch {}
  clearInterval(backoffTimer); backoffTimer = null;
  firstBlobSeen = false;

  rec = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 128000 });

  rec.ondataavailable = async (ev) => {
    const blob = ev.data;
    if (!blob || !blob.size) return;

    if (!firstBlobSeen){
      if (blob.size < 32) return;
      firstBlobSeen = true;
    }

    const HIGH_WATER = 1.5 * 1024 * 1024; // 1.5MB
    if (bufferedAmount() > HIGH_WATER){
      try { rec?.pause(); } catch {}
      clearInterval(backoffTimer);
      backoffTimer = setInterval(()=>{
        if (bufferedAmount() < 256 * 1024){
          clearInterval(backoffTimer); backoffTimer = null;
          try { rec?.resume(); } catch {}
        }
      }, 100);
    }

    try { await sendAudioChunk(blob); } catch (e) {
      console.warn('[voice] sendAudioChunk failed', e);
    }
  };

  rec.onerror = (e) => console.warn('[voice] recorder error', e);
  rec.start(200); // 200ms timeslice
}

/** Stop streaming and release timers; keep the stream for reuse. */
export function disarmVAD(){
  try { rec?.stop(); } catch {}
  rec = null;
  if (backoffTimer){ clearInterval(backoffTimer); backoffTimer = null; }
  firstBlobSeen = false;
}

/** Politely cut the assistant and hand the floor to the user. */
export function bargeIn(){
  try { sendCloseStream(); } catch {}
}

/** No-op for parity. */
export function setVadBoost(_v){}
