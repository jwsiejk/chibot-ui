// app.js — session control and typed chat
import { installFetchInterceptor, ensureCSRF } from './csrf.js';
import { openWS, waitWSOpen, closeWS } from './ws.js';
import { initMic, armVAD, disarmVAD, getCurrentStream } from './voice.js';
import { unlockAudio } from './audio.js';

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

async function onStart(){
  try{
    installFetchInterceptor();
    await ensureCSRF();
    await unlockAudio();
    openWS();
    await waitWSOpen();
    const stream = await initMic();
    await armVAD(stream);
    setDot('listening');
    const endBtn = document.getElementById('endButton');
    const startBtn = document.getElementById('startButton');
    if (endBtn) endBtn.disabled = false;
    if (startBtn) startBtn.disabled = true;
    // Fire greet after WS up so audio can stream
    fetch('/api/v1/greet', { credentials:'include' }).catch(()=>{});
  }catch(e){
    console.error('[app] start failed', e);
  }
}

async function onEnd(){
  try{ disarmVAD(); closeWS(); }catch{}
  setDot('ready');
  const endBtn = document.getElementById('endButton');
  const startBtn = document.getElementById('startButton');
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

  const sid = localStorage.getItem('chip.sid') || '';
  const headers = new Headers({ 'Content-Type':'application/json' });
  const csrf = await ensureCSRF().catch(()=>'');
  if (csrf) headers.set('X-CSRF-Token', csrf);
  try{
    const idem = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    headers.set('Idempotency-Key', String(idem));
  }catch{}
  fetch('/api/v1/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({ text, session_id: sid }),
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
