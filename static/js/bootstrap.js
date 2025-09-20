// /static/js/bootstrap.js — single owner of Start/End/Send + audio unlock + WS→UI wiring
import { openWS, waitWSOpen, isOpen } from '/static/js/ws.js?v=v20250911b';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js?v=v20250911b';
import { initMic } from '/static/js/voice.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';
import { onEnd, onSend, handleAssistantFrame } from '/static/js/app.js?v=v20250911b';
import { unlockAudio } from '/static/js/audio.js?v=v20250911b';

const $ = (s)=>document.querySelector(s);

let startInFlight = false;
let started = false;

function setDot(state){
  const dot = $('#stateDot'); if (!dot) return;
  dot.className = 'dot ' + (
    state==='listening' ? 'dot-listening' :
    state==='speaking'  ? 'dot-speaking'  :
    state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
}

function showBanner(msg){
  let b = $('#inlineLoginMsg');
  if (!b) return console.warn('[AskChip]', msg);
  b.textContent = msg;
  b.classList.add('warn');
}

function wireWSEventsOnce(){
  if (window.__askchip_ws_wired) return;
  window.__askchip_ws_wired = true;
  window.addEventListener('askchip-ws', (ev)=>{
    try { handleAssistantFrame(ev.detail); } catch(e){ console.warn('handleAssistantFrame error', e); }
  });
  window.addEventListener('askchip-ws-close', (ev)=>{
    // Optional: reflect disconnected state
    // setDot('ready');
  });
}

async function ensureWsOpenOrFail(timeoutMs=5000){
  if (isOpen()) return true;
  openWS();
  try {
    await Promise.race([
      waitWSOpen(),
      new Promise((_,rej)=>setTimeout(()=>rej(new Error('WS timeout')), timeoutMs))
    ]);
  } catch {
    return false;
  }
  return isOpen();
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

    // 0) Audio unlock so TTS can play
    try { await unlockAudio(); } catch {}

    // 1) Network/CSRF prep
    try { installFetchInterceptor(); } catch {}
    try { await ensureCSRF(); } catch {}

    // 2) WS first — verify actually OPEN or abort
    const ok = await ensureWsOpenOrFail(5000);
    if (!ok){
      showBanner('WebSocket did not open — greet aborted.');
      setDot('ready');
      if (startBtn) startBtn.disabled = false;
      startInFlight = false;
      return;
    }

    // 3) Wire WS → UI events exactly once
    wireWSEventsOnce();

    // 4) Mic permission (best effort)
    try { await initMic(); } catch {}

    // 5) Now greet, same session id as the WS
    const sid = getSID();
    await fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(sid)}`, {
      credentials: 'include'
    });

    // Mark session active so ws.js auto-reconnects after restarts
    window.__askchip_session_started = true;

    // Ready for user input
    if (endBtn)  endBtn.disabled  = false;
    if (sendBtn) sendBtn.disabled = false;
    started = true;

  } catch(e){
    console.error('[bootstrap] start failed', e);
    if (startBtn) startBtn.disabled = false;
    setDot('ready');
  } finally {
    startInFlight = false;
  }
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

  // breadcrumb so we can confirm bootstrap actually loaded
  window.__askchip_bootstrap_loaded = true;
  console.log('[AskChip] bootstrap loaded');
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireUI);
else wireUI();
