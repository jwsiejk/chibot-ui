// static/js/app.js — session control and typed chat (production-deterministic)
// Ensures: open WS → await open → greet → arm mic (no lost frames).
// Renders assistant frames regardless of TTS availability.

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
    openWS();
    await waitWSOpen();

    // 2) Prime mic permission early; VAD arms later
    await initMic().catch(()=>{ /* ignore mic deny for greet text */ });

    // 3) Call greet with the SAME SID the WS is using
    fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(getSID())}`, {
      credentials: 'include'
    }).catch(()=>{});

    setDot('thinking'); // will flip via ws events

    const endBtn = $('#endButton');
    const startBtn = $('#startButton');
    if (endBtn) endBtn.disabled = false;
    if (startBtn) startBtn.disabled = true;

  }catch(e){
    console.warn('[start] failed', e);
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

// Phase 4+: wire UI state to WS events
window.addEventListener('askchip-ws', (ev)=>{
  const msg = ev.detail || {};

  // State dot
  if (msg.type === 'Results'){
    setDot('listening');
  } else if (msg.type === 'UtteranceEnd'){
    setDot('thinking');
  }

  // Assistant flow (text-first regardless of TTS)
  if (msg.type === 'assistant_chunk'){
    window.__ac_text = (window.__ac_text || '') + String(msg.text||'');
  } else if (msg.type === 'assistant_end'){
    const text = (window.__ac_text || '').trim(); window.__ac_text = '';
    if (text){ addChatMessage('assistant', text); }
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
  const sendBtn  = $('#composerSend');
  if (startBtn) startBtn.addEventListener('click', onStart);
  if (sendBtn)  sendBtn.addEventListener('click', onSend);
  setDot('ready');
});
