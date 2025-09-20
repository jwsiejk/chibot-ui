// bootstrap.js — single owner of Start/End/Send + audio unlock + single-flight Start
import { openWS, waitWSOpen } from '/static/js/ws.js?v=v20250911b';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js?v=v20250911b';
import { initMic } from '/static/js/voice.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';
import { onEnd, onSend } from '/static/js/app.js?v=v20250911b';
import { unlockAudio } from '/static/js/audio.js?v=v20250911b';

const $ = (s)=>document.querySelector(s);

let startInFlight = false;
let started = false;

function setDot(state){
  const dot = document.getElementById('stateDot');
  if (!dot) return;
  dot.className = 'dot ' + (
    state==='listening' ? 'dot-listening' :
    state==='speaking'  ? 'dot-speaking'  :
    state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
}

async function startOnce(){
  if (started || startInFlight) return;
  startInFlight = true;
  setDot('thinking');

  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  try{
    if (startBtn) startBtn.disabled = true;

    try { await unlockAudio(); } catch {}
    try { installFetchInterceptor(); } catch {}
    try { await ensureCSRF(); } catch {}

    openWS();
    await waitWSOpen();

    try { await initMic(); } catch {}

    const sid = getSID();
    await fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(sid)}`, {
      credentials: 'include'
    });

    if (endBtn)  endBtn.disabled  = false;
    if (sendBtn) sendBtn.disabled = false;
    started = true;
  } catch (e){
    console.error('[bootstrap] start failed', e);
    startInFlight = false;
    if (startBtn) startBtn.disabled = false;
    setDot('ready');
    return;
  }
  startInFlight = false;
  setDot('ready');
}

function wireUI(){
  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  const form     = $('#composerForm');

  if (startBtn) startBtn.addEventListener('click', startOnce);
  if (endBtn)   endBtn.addEventListener('click', onEnd);
  if (sendBtn)  sendBtn.addEventListener('click', onSend);
  if (form)     form.addEventListener('submit', (e)=>{ e.preventDefault(); onSend(); });

  if (sendBtn) sendBtn.disabled = true;
  if (endBtn)  endBtn.disabled  = true;
  setDot('ready');
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireUI);
else wireUI();
