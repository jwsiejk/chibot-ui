// static/js/app.js — side-effect-free chat helpers + render with single-bubble coalescing
export { openWS, waitWSOpen } from '/static/js/ws.js?v=v20250911b';
export { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js?v=v20250911b';
export { initMic } from '/static/js/voice.js?v=v20250911b';
export { getSID } from '/static/js/util/sid.js';

import '/static/js/csrf.js?v=v20250911b';
import '/static/js/audio.js?v=v20250911b';
import '/static/js/voice.js?v=v20250911b';
import '/static/js/ws.js?v=v20250911b';
import '/static/js/ui_menu.js?v=v20250911b';
import '/static/js/auth_gate.js?v=v20250911b';

import { installFetchInterceptor, ensureCSRF } from '/static/js/csrf.js?v=v20250911b';
import { openWS, waitWSOpen, closeWS } from '/static/js/ws.js?v=v20250911b';
import { initMic } from '/static/js/voice.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';

// ---------- UI helpers ----------
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

function addChatBubble(role, text){
  const box = document.getElementById('chatMessages');
  if (!box) return null;
  const el = document.createElement('div');
  el.className = 'msg ' + (role==='user' ? 'user' : 'assistant');
  el.textContent = text;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
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

// ---------- Single-bubble coalescing for assistant turns ----------
const turnState = new Map(); // turn_id -> { el, final:boolean, text:string, ttsStarted:boolean }

function upsertAssistantTurn(turnId, text, isFinal){
  const box = document.getElementById('chatMessages'); if (!box) return;
  let st = turnState.get(turnId);
  if (!st){
    const el = addChatBubble('assistant', text || '');
    st = { el, final:false, text:text||'', ttsStarted:false };
    turnState.set(turnId, st);
  } else {
    // Update the existing bubble (don’t create another)
    st.text = text || st.text;
    if (st.el) st.el.textContent = st.text;
  }
  if (isFinal) st.final = true;
  return st;
}

// Optional client-side TTS helper (fallback if server’s scheduled audio doesn’t play)
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
      const mod = await import('/static/js/audio.js?v=v20250911b');
      if (mod.playBytesStream) await mod.playBytesStream(bytes);
      else if (mod.playBytesB64) await mod.playBytesB64(b64);
    }
  }catch(e){
    console.warn('tts error', e);
  }
}

// Expose handlers used by bootstrap
export async function onEnd(){
  const headers = new Headers({ 'Content-Type':'application/json' });
  try{
    const csrf = await ensureCSRF().catch(()=> '');
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }catch{}

  const body = { type:'EndSession', session_id: getSID() };

  try{
    const resp = await fetch('/api/v1/chat', {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify(body)
    });
    if (!resp.ok){
      const txt = await resp.text().catch(()=> '');
      console.error('/api/v1/chat EndSession failed', resp.status, txt);
    }
  }finally{
    try { closeWS(); } catch {}
    setDot('ready');
  }
}

export async function onSend(){
  const inp = document.getElementById('composer');
  const text = (inp && inp.value || '').trim();
  if (!text) return;
  if (inp) inp.value = '';
  addChatBubble('user', text);

  const headers = new Headers({ 'Content-Type':'application/json' });
  try{
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
    body: JSON.stringify({ type:'UserText', text, session_id: getSID() }),
    credentials: 'include'
  }).catch(console.warn);

  setDot('thinking');
}

// ---------- WS event hook (called by ws.js) ----------
/**
 * Call this from ws.js when receiving frames.
 * Expected msg shape examples:
 *  - { type:'assistant_text', turn_id, text }
 *  - { type:'assistant_final', turn_id, text }
 *  - { type:'suggestions', items:[...] }
 *  - { type:'state', phase:'assistant_speaking' | 'ready' | ... }
 */
export function handleAssistantFrame(msg){
  if (!msg || !msg.type) return;

  if (msg.type === 'assistant_text'){
    if (!msg.turn_id) return;
    upsertAssistantTurn(msg.turn_id, msg.text || '', false);
    setDot('speaking');
    return;
  }

  if (msg.type === 'assistant_final'){
    if (!msg.turn_id) return;
    const st = upsertAssistantTurn(msg.turn_id, msg.text || '', true);

    // If server-side audio didn’t start, do a client-side TTS fallback once per turn
    if (st && !st.ttsStarted && st.text){
      st.ttsStarted = true;
      // fire-and-forget; don’t block UI
      speakText(st.text).catch(()=>{});
    }
    setDot('ready');
    return;
  }

  if (msg.type === 'suggestions' && Array.isArray(msg.items)){
    setSuggestions(msg.items);
    return;
  }

  if (msg.type === 'state'){
    if (msg.phase === 'assistant_speaking') setDot('speaking');
    if (msg.phase === 'assistant_end' || msg.phase === 'ready') setDot('ready');
    return;
  }

  if (msg.type === 'Error'){
    console.warn('[ws] server error:', msg.code, msg.message);
  }
}
