// app.js — session control and typed chat
import { installFetchInterceptor, ensureCSRF } from './csrf.js';
import { openWS, waitWSOpen, closeWS } from './ws.js';
import { initMic, disarmVAD, bargeIn } from './voice.js';
import { unlockAudio, stopPlayback } from './audio.js';
import { getSID } from './util/sid.js';

const $ = (s)=>document.querySelector(s);

async function _fetchWSToken(sid){
  try{
    const r = await fetch(`/api/v1/auth/ws-token?session_id=${encodeURIComponent(sid)}`, { credentials: 'include' });
    const j = await r.json();
    return j && j.token;
  }catch(e){ console.warn('ws-token fetch failed', e); return null; }
}


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

async function onStart(){
  try{
    installFetchInterceptor();
    await ensureCSRF();
    await unlockAudio();

    // Open WS with auth and wait, so greet frames have a subscriber
    const sid = getSID();
    const _tok = await _fetchWSToken(sid);
    openWS(_tok);
    await waitWSOpen();

    // Prime mic permission once; VAD will arm when assistant is ready
    await initMic();

    // Call greet with the SAME SID the WS is using
    fetch(`/api/v1/greet?session_id=${encodeURIComponent(getSID())}`, {
      credentials: 'include'
    }).catch(()=>{});

    setDot('thinking'); // will flip to speaking/listening via ws events
    const endBtn = $('#endButton');
    const startBtn = $('#startButton');
    if (endBtn) endBtn.disabled = false;
    if (startBtn) startBtn.disabled = true;
  }catch(e){
    console.error('[app] start failed', e);
  }
}

async function onEnd(){
  try{ stopPlayback(); }catch{}

  try{ bargeIn(); }catch{}

  try{ disarmVAD(); closeWS(); }catch{}
  setDot('ready');
  const endBtn = $('#endButton');
  const startBtn = $('#startButton');
  if (startBtn) startBtn.disabled = false;
  if (endBtn)   endBtn.disabled = true;
}

async function onSend(){
  try{
    installFetchInterceptor();
    await ensureCSRF();
  }catch{}
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

document.addEventListener('DOMContentLoaded', () => {
  const startBtn = document.getElementById('startButton');
  const endBtn   = document.getElementById('endButton');
  const sendBtn  = document.getElementById('composerSend');
  if (startBtn) startBtn.addEventListener('click', onStart);
  if (endBtn)   endBtn.addEventListener('click', onEnd);
  if (sendBtn)  sendBtn.addEventListener('click', onSend);
  setDot('ready');
});


// Phase 4: wire UI state to WS events
window.addEventListener('askchip-ws', (ev)=>{
  const msg = ev.detail || {};
  const dot = document.getElementById('stateDot');
  if (!dot) return;
  if (msg.type === 'Results'){
    dot.className = 'dot dot-listening'; // still speaking/interims
  } else if (msg.type === 'UtteranceEnd'){
    dot.className = 'dot dot-thinking';
  } else if (msg.type === 'Error'){
    // fall-through below
  }
  // Additional WS frames
  if (msg.type === 'suggestions' && Array.isArray(msg.items)){
    setSuggestions(msg.items);
  } else if (msg.type === 'assistant_chunk'){
    window.__ac_text = (window.__ac_text || '') + String(msg.text||'');
  } else if (msg.type === 'assistant_end'){
    const text = (window.__ac_text || '').trim(); window.__ac_text = '';
    if (text){ addChatMessage('assistant', text); try{ speakText(text); }catch{} }
    if (typeof setDot==='function') setDot('ready');
  } else if (msg.type === 'state'){
    if (msg.phase === 'assistant_speaking' && typeof setDot==='function') setDot('speaking');
    if ((msg.phase === 'assistant_end' || msg.phase === 'ready') && typeof setDot==='function') setDot('ready');
  } else if (msg.type === 'Error'){
    dot.className = 'dot dot-ready';
  }
});


document.addEventListener('keydown', (e)=>{
  if (e.key === 'Escape'){
    try{ stopPlayback(); }catch{}
    try{ bargeIn(); }catch{}
  }
});

function appendMessage(role, text){
  const box = document.getElementById('chatMessages'); if (!box) return;
  const el = document.createElement('div');
  el.className = 'msg ' + (role==='user' ? 'user' : 'assistant');
  el.textContent = (text||'').trim();
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function setSuggestions(items){
  const ul = document.getElementById('sugg'); if (!ul) return;
  ul.innerHTML='';
  (items||[]).slice(0,4).forEach(t=>{
    const li=document.createElement('li'); const b=document.createElement('button');
    b.textContent=t; b.addEventListener('click',()=>{ const i=document.getElementById('composer'); if(i){ i.value=t; i.focus(); }});
    li.appendChild(b); ul.appendChild(li);
  });
}

async function speakText(text){
  try{
    const r = await fetch('/api/v1/voice/tts-with-visemes', { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({ text }) });
    const j = await r.json(); const b64 = (j && j.audio_b64) || '';
    if (b64){ const bytes = Uint8Array.from(atob(b64), c=>c.charCodeAt(0)); const mod = await import('./audio.js'); await mod.playBytesStream(bytes); }
  }catch(e){ console.warn('[tts] synth failed', e); }
}
