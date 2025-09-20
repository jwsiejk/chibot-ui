// Single-source bootstrap: Start/End/Send wiring with single-flight + audio unlock

import {
  openWS, waitWSOpen
} from '/static/js/ws.js?v=v20250911b';
import {
  ensureCSRF, installFetchInterceptor
} from '/static/js/csrf.js?v=v20250911b';
import { initMic } from '/static/js/voice.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';
import { onEnd, onSend } from '/static/js/app.js?v=v20250911b';
import { unlockAudio } from '/static/js/audio.js?v=v20250911b';

const $ = (s)=>document.querySelector(s);

// --- single-flight state
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

async function startOnce() {
  if (started || startInFlight) return;
  startInFlight = true;
  setDot('thinking');

  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  try {
    if (startBtn) startBtn.disabled = true;

    // 0) Audio unlock on FIRST user gesture so TTS can play
    try { await unlockAudio(); } catch {}

    // 1) CSRF/fetch interceptor (safe even if greet is GET)
    try { installFetchInterceptor(); } catch {}
    try { await ensureCSRF(); } catch {}

    // 2) Open a single WS (idempotent if already open)
    openWS();
    await waitWSOpen();   // resolves if already open

    // 3) Prime mic permission (don’t fail the flow if denied)
    try { await initMic(); } catch {}

    // 4) Now greet using the SAME session id
    const sid = getSID();
    await fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(sid)}`, {
      credentials: 'include'
    });

    // ready for user
    if (endBtn)  endBtn.disabled  = false;
    if (sendBtn) sendBtn.disabled = false;
    started = true;

  } catch (e) {
    console.error('[bootstrap] start failed', e);
    // allow retry if we failed before greet
    startInFlight = false;
    if (startBtn) startBtn.disabled = false;
    setDot('ready');
    return;
  }
  startInFlight = false;
  setDot('ready');
}

function wireUI() {
  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  const form     = $('#composerForm');

  if (startBtn) startBtn.addEventListener('click', startOnce);
  if (endBtn)   endBtn.addEventListener('click', onEnd);
  if (sendBtn)  sendBtn.addEventListener('click', onSend);
  if (form)     form.addEventListener('submit', (e)=>{ e.preventDefault(); onSend(); });

  // initial states
  if (sendBtn) sendBtn.disabled = true;
  if (endBtn)  endBtn.disabled  = true;
  setDot('ready');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', wireUI);
} else {
  wireUI();
}
