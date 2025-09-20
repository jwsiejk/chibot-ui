// static/js/app.js — production-safe module (no top-level conflicts)
// Exports used by templates/index.html inline bootstrap
export { openWS, waitWSOpen } from '/static/js/ws.js?v=v20250911b';
export { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js?v=v20250911b';
export { initMic } from '/static/js/voice.js?v=v20250911b';
export { getSID } from '/static/js/util/sid.js';

// Side-effect imports once (idempotent modules)
import '/static/js/csrf.js?v=v20250911b';
import '/static/js/audio.js?v=v20250911b';
import '/static/js/voice.js?v=v20250911b';
import '/static/js/ws.js?v=v20250911b';
import '/static/js/ui_menu.js?v=v20250911b';
import '/static/js/auth_gate.js?v=v20250911b';

// No further imports below this line.

// ---- UI helpers
const $ = (s)=>document.querySelector(s);

function setDot(state){
  const dot = document.getElementById('stateDot');
  if (!dot) return;
  dot.className = 'dot ' + (
    state==='listening' ? 'dot-listening' :
    state==='speaking'  ? 'dot-speaking'  :
    state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
  const label = document.getElementById('statusText');
  if (label){
    label.textContent = state.charAt(0).toUpperCase()+state.slice(1);
  }
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
    li.appendChild(b);
    ul.appendChild(li);
  });
}

// ---- Session controls (start is handled by templates/index.html token bootstrap)
export async function onEnd(){
  try{
    fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type':'application/json' },
      credentials: 'include',
      body: JSON.stringify({ type:'EndSession' })
    }).catch(()=>{});
  }finally{
    try {
      const { closeWS } = await import('/static/js/ws.js?v=v20250911b');
      closeWS();
    } catch {}
    setDot('ready');
  }
}

export async function onSend(){
  const inp = document.getElementById('composer');
  const text = (inp && inp.value || '').trim();
  if (!text) return;
  if (inp) inp.value = '';
  addChatMessage('user', text);

  const headers = new Headers({ 'Content-Type':'application/json' });
  try{
    const { ensureCSRF } = await import('/static/js/csrf.js?v=v20250911b');
    const csrf = await ensureCSRF().catch(()=> '');
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }catch{}
  try{
    const idem = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    headers.set('Idempotency-Key', String(idem));
  }catch{}
  fetch('/api/v1/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({ type:'UserText', text }),
    credentials: 'include'
  }).catch(console.warn);

  setDot('thinking');
}

// Optional client-side TTS helper
export async function speakText(text){
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
      const mod = await import('/static/js/audio.js?v=v20250911b');
      if (mod.playBytesStream) await mod.playBytesStream(bytes);
      else if (mod.playBytesB64) await mod.playBytesB64(b64);
    }
  }catch(e){
    console.warn('tts error', e);
  }
}

// ---- Boot (do not attach Start handler here; inline bootstrap owns it)
document.addEventListener('DOMContentLoaded', ()=>{
  const endBtn   = $('#endButton');
  const sendBtn  = $('#composerSend');
  if (endBtn)  endBtn.addEventListener('click', onEnd);
  if (sendBtn) sendBtn.addEventListener('click', onSend);
  const form = document.getElementById('composerForm');
  if (form) form.addEventListener('submit', (e)=>{ e.preventDefault(); onSend(); });
  setDot('ready');
  setSuggestions(['What can you do?', 'Explain FlashArray install', 'Teach me Portworx', 'Compare solutions']);
});
