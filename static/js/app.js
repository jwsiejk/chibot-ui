// static/js/app.js — session control and typed chat (production)
// Deterministic Start: open WS → await open → greet → arm mic.
// Proper End handler: tells server to end_session and resets UI.
// Text-first: assistant frames render regardless of TTS availability.

import { installFetchInterceptor, ensureCSRF } from './csrf.js';
import { openWS, waitWSOpen, closeWS } from './ws.js';
import { initMic } from './voice.js';
import { getSID } from './util/sid.js';

const $ = (s)=>document.querySelector(s);

function setDot(state){
  const dot = document.getElementById('stateDot');
  if (!dot) return;
  dot.className = 'dot ' + (
    state==='listening' ? 'dot-listening' :
    state==='speaking'  ? 'dot-speaking'  :
    state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
}

function addChatMessage(role, text){
  const box = document.getElementById('chatMessages');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'msg ' + (role==='user' ? 'user' : 'assistant');
  el.textContent = text;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function setSuggestions(items){
  const ul = document.getElementById('sugg'); if (!ul) return;
  ul.innerHTML = '';
  (items||[]).slice(0,4).forEach(t=>{
    const li = document.createElement('li');
    const b  = document.createElement('button');
    b.textContent = t;
    b.addEventListener('click', ()=>{
      const i = document.getElementById('composer');
      if (i){ i.value = t; i.focus(); }
    });
    li.appendChild(b); ul.appendChild(li);
  });
}

async function onStart(){
  try{
    installFetchInterceptor();
    await ensureCSRF();

    // 1) Open WS and wait until subscribed (prevents greet frames from being missed)
    const sid = getSID();
const tok = await fetch(`/api/v1/auth/ws-token?session_id=${encodeURIComponent(sid)}`, { credentials: 'include' }).then(r=>r.json());
if (!tok || !tok.ok || !tok.token) { throw new Error('ws-token fetch failed'); }
openWS(tok.token);
await waitWSOpen();

    // 2) Prime mic permission early; VAD arms later
    await initMic().catch(()=>{ /* ignore mic deny for greet text */ });

    // 3) Call greet with the SAME SID the WS is using
    fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(getSID())}`, {
      credentials: 'include'
    }).catch(()=>{});

    // Toggle buttons
    const endBtn = $('#endButton');
    const startBtn = $('#startButton');
    if (endBtn) endBtn.disabled = false;
    if (startBtn) startBtn.disabled = true;

    setDot('thinking'); // will flip via ws events
  }catch(e){
    console.warn('[start] failed', e);
  }
}

async function onEnd(){
  try{
    const headers = new Headers({ 'Content-Type':'application/json' });
    const csrf = await ensureCSRF().catch(()=> '');
    if (csrf) headers.set('X-CSRF-Token', csrf);

    // Tell server to end session (server also clears greet idempotency)
    await fetch('/api/v1/chat', {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({ cmd: 'end_session', session_id: getSID() })
    });

    // Close WS and reset UI state
    try { closeWS && closeWS(); } catch {}
    const endBtn = $('#endButton');
    const startBtn = $('#startButton');
    if (endBtn) endBtn.disabled = true;
    if (startBtn) startBtn.disabled = false;
    setDot('ready');
  }catch(e){
    console.warn('[end] failed', e);
  }
}

async function onSend(){
  const inp = document.getElementById('composer');
  const text = (inp && inp.value || '').trim();
  if (!text) return;
  if (inp) inp.value = '';
  addChatMessage('user', text);

  const headers = new Headers({ 'Content-Type':'application/json' });
  const csrf = await ensureCSRF().catch(()=> '');
  if (csrf) headers.set('X-CSRF-Token', csrf);
  try{
    const idem = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    headers.set('Idempotency-Key', String(idem));
  }catch{}
  fetch('/api/v1/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({ text, session_id: getSID() }),
    credentials: 'include'
  }).catch(console.warn);

  setDot('thinking');
}

// Optional client-side TTS helper; safe to keep even if server streams audio separately.
async function speakText(text){
  try{
    const r = await fetch('/api/v1/voice/tts-with-visemes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ text })
    });
    const j = await r.json(); const b64 = (j && j.audio_b64) || '';
    if (b64){
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const mod = await import('./audio.js');
      if (mod.playBytesStream) await mod.playBytesStream(bytes);
      else if (mod.playBytesB64) await mod.playBytesB64(b64);
    }
  }catch(e){ console.warn('[tts] synth failed', e); }
}

// WS event wiring
window.addEventListener('askchip-ws', (ev)=>{
  const msg = ev.detail || {};

  // Minimal console tracing to confirm frames are arriving
  try{
    if (msg && msg.type) console.debug('[ws<-]', msg.type, msg);
  }catch{}

  if (msg.type === 'Results'){
    setDot('listening');
  } else if (msg.type === 'UtteranceEnd'){
    setDot('thinking');
  }

  if (msg.type === 'assistant_chunk'){
    window.__ac_text = (window.__ac_text || '') + String(msg.text||'');
  } else if (msg.type === 'assistant_end'){
    const text = (window.__ac_text || '').trim(); window.__ac_text = '';
    if (text){
      addChatMessage('assistant', text);
      try{ speakText(text); }catch{}
    }
    setDot('ready');
  } else if (msg.type === 'suggestions' && Array.isArray(msg.items)){
    setSuggestions(msg.items);
  } else if (msg.type === 'state'){
    if (msg.phase === 'assistant_speaking') setDot('speaking');
    if (msg.phase === 'assistant_end' || msg.phase === 'ready') setDot('ready');
  } else if (msg.type === 'Error'){
    console.warn('[ws] server error:', msg.code, msg.message);
  }
});

document.addEventListener('DOMContentLoaded', ()=>{
  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  if (startBtn) startBtn.addEventListener('click', onStart);
  if (endBtn)   endBtn.addEventListener('click', onEnd);
  if (sendBtn)  sendBtn.addEventListener('click', onSend);
  setDot('ready');
});
