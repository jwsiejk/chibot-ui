// Clean, safe app.js (no optional-chaining on assignment)
import { installFetchInterceptor, ensureCSRF } from './csrf.js';
import { openWS, waitWSOpen, closeWS } from './ws.js';
import { initMic, armVAD, disarmVAD } from './voice.js';

const $ = (s)=>document.querySelector(s);

function setDot(state){
  const dot = document.getElementById('stateDot');
  if (dot){
    dot.className = 'dot ' + (
      state==='listening' ? 'dot-listening' :
      state==='speaking'  ? 'dot-speaking'  :
      state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
    );
  }
  const st = document.getElementById('statusText');
  if (st) st.textContent = state.charAt(0).toUpperCase() + state.slice(1);
}

function addChatMessage(role, text){
  const box = document.getElementById('chatMessages');
  if (!box) return;
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function onStart(){
  try{
    installFetchInterceptor();
    await ensureCSRF();
  }catch{}
  try{
    // Ensure SID
    if (!localStorage.getItem('chip.sid')) localStorage.setItem('chip.sid', crypto.randomUUID());

    // WS + mic
    openWS();
    await waitWSOpen();
    await initMic();
    // VAD will arm from WS state frames after greet

    // Greet
    const sid = localStorage.getItem('chip.sid');
    await fetch(`/api/v1/greet?session_id=${encodeURIComponent(sid)}`, { credentials:'include' });

    setDot('listening');
    const endBtn = document.getElementById('endButton');
    const startBtn = document.getElementById('startButton');
    if (endBtn) endBtn.disabled = false;
    if (startBtn) startBtn.disabled = true;
  }catch(e){
    console.error('start failed', e);
  }
}

async function onEnd(){
  try{ disarmVAD(); closeWS(); setDot('ready'); }catch{}
  const endBtn = document.getElementById('endButton');
  const startBtn = document.getElementById('startButton');
  if (startBtn) startBtn.disabled = false;
  if (endBtn) endBtn.disabled = true;
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
  const csrf = (document.head.querySelector('meta[name=csrf]') || {}).content || '';
  await fetch('/api/v1/chat', {
    method:'POST',
    credentials:'include',
    headers:{ 'Content-Type':'application/json','X-CSRF-Token': csrf },
    body: JSON.stringify({ text, session_id: sid })
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
