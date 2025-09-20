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
  (items || []).slice(0,4).forEach(item=>{
    const label = (typeof item === 'string') ? item : (item?.label ?? item?.text ?? String(item));
    const value = (typeof item === 'string') ? item : (item?.value ?? label);
    const li = document.createElement('li');
    const b  = document.createElement('button');
    b.textContent = label;
    b.addEventListener('click', ()=>{
      const i = document.getElementById('composer');
      if (i){ i.value = value; i.focus(); }
    });
    li.appendChild(b);
    ul.appendChild(li);
  });
}

// ---------- Single-bubble coalescing for assistant turns ----------
const turnState = new Map(); // turn_id -> { el, final:boolean, text:string, ttsStarted:boolean }
const turnDebounce = new Map(); // turn_id -> timeout handle

function upsertAssistantTurn(turnId, text, isFinal){
  const box = document.getElementById('chatMessages'); if (!box) return;
  let st = turnState.get(turnId);
  if (!st){
    const el = addChatBubble('assistant', text || '');
    st = { el, final:false, text:text||'', ttsStarted:false };
    turnState.set(turnId, st);
  } else {
    // Update the existing bubble (don’t create another)
    st.text = (text != null) ? text : st.text;
    if (st.el) st.el.textContent = st.text;
  }
  if (isFinal) st.final = true;
  return st;
}

// Optional client-side TTS helper (fallback if server’s scheduled audio doesn’t play)
async function speakText(text){
  if (!text) return;
  try{
    const headers = new Headers({ 'Content-Type': 'application/json' });
    try {
      const csrf = await ensureCSRF().catch(()=> '');
      if (csrf) headers.set('X-CSRF-Token', csrf);
    } catch {}

    const resp = await fetch('/api/v1/voice/tts-with-visemes', {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({ text })
    });

    if (!resp.ok){
      const t = await resp.text().catch(()=> '');
      console.error('[tts] HTTP', resp.status, t);
      return;
    }

    const j = await resp.json().catch(e=>{ console.error('[tts] bad JSON', e); return null; });
    const b64 = j && j.audio_b64;
    if (!b64){
      console.warn('[tts] no audio_b64 in response');
      return;
    }

    try{
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const mod = await import('/static/js/audio.js?v=v20250911b');
      if (mod.playBytesStream) { await mod.playBytesStream(bytes); return; }
      if (mod.playBytesB64)    { await mod.playBytesB64(b64);    return; }
    }catch(e){
      console.warn('[tts] WebAudio path failed, falling back to <audio>', e);
    }

    try{
      const binary = atob(b64);
      const len = binary.length;
      const buf = new Uint8Array(len);
      for (let i=0;i<len;i++) buf[i] = binary.charCodeAt(i);
      const blob = new Blob([buf], { type: 'audio/mpeg' });
      const url  = URL.createObjectURL(blob);
      const a = new Audio(url);
      a.onended = ()=> URL.revokeObjectURL(url);
      await a.play();
    }catch(e){
      console.error('[tts] <audio> playback failed', e);
    }

  }catch(e){
    console.error('[tts] unexpected error', e);
  }
});
    // Add CSRF (many servers require it even for POST JSON)
    try {
      const csrf = await ensureCSRF().catch(()=> '');
      if (csrf) headers.set('X-CSRF-Token', csrf);
    } catch {}

    const resp = await fetch('/api/v1/voice/tts-with-visemes', {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({ text })
    });

    if (!resp.ok){
      const t = await resp.text().catch(()=> '');
      console.error('[tts] HTTP', resp.status, t);
      return;
    }

    const j = await resp.json().catch(e=>{ console.error('[tts] bad JSON', e); return null; });
    const b64 = j && j.audio_b64;
    if (!b64){
      console.warn('[tts] no audio_b64 in response');
      return;
    }

    // Primary path: WebAudio helpers (if available)
    try{
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const mod = await import('/static/js/audio.js?v=v20250911b');
      if (mod.playBytesStream) { await mod.playBytesStream(bytes); return; }
      if (mod.playBytesB64)    { await mod.playBytesB64(b64);    return; }
    }catch(e){
      console.warn('[tts] WebAudio path failed, falling back to <audio>', e);
    }

    // Fallback path: HTMLAudioElement (broad compatibility)
    try{
      const binary = atob(b64);
      const len = binary.length;
      const buf = new Uint8Array(len);
      for (let i=0;i<len;i++) buf[i] = binary.charCodeAt(i);
      const blob = new Blob([buf], { type: 'audio/mpeg' });
      const url  = URL.createObjectURL(blob);
      const a = new Audio(url);
      a.onended = ()=> URL.revokeObjectURL(url);
      await a.play();
    }catch(e){
      console.error('[tts] <audio> playback failed', e);
    }

  }catch(e){
    console.error('[tts] unexpected error', e);
  }
}

// Expose handlers used by bootstrap
export async function onEnd(){
  const headers = new Headers({ 'Content-Type':'application/json' });
  try{
    const csrf = await ensureCSRF().catch(()=> '');
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }catch{}
  try{
    const idem = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    headers.set('Idempotency-Key', String(idem));
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

  try{
    const resp = await fetch('/api/v1/chat', {
      method: 'POST',
      headers,
      body: JSON.stringify({ type:'UserText', text, session_id: getSID() }),
      credentials: 'include'
    });
    if (!resp.ok){
      const t = await resp.text().catch(()=> '');
      console.error('/api/v1/chat UserText failed', resp.status, t);
    }
  }catch(e){
    console.error('/api/v1/chat send failed', e);
  }

  setDot('thinking');
});
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
 * Handles: assistant_chunk (streaming), assistant_text, assistant_final,
 * generic role:'assistant', as well as suggestions/state/Error.
 */
export function handleAssistantFrame(msg){
  if (!msg) return;

  // Normalize to {turnId, text, isFinal}
  let turnId = msg.turn_id || msg.turnId || msg.id || 'greet';
  let text = '';
  let isFinal = false;

  // 1) Preferred explicit types
  if (msg.type === 'assistant_text'){ text = msg.text || ''; isFinal = false; }
  else if (msg.type === 'assistant_final'){ text = msg.text || ''; isFinal = true; }
  // 2) Your greet stream shows type: 'assistant_chunk'
  else if (msg.type === 'assistant_chunk'){ text = msg.text || ''; isFinal = false; }
  // 3) Generic assistant role
  else if (msg.role === 'assistant'){ text = msg.content || msg.message || ''; isFinal = true; }
  // 4) content arrays (OpenAI-style)
  else if (Array.isArray(msg.content)){
    const t = msg.content.find(c => (c.type === 'text' && c.text))?.text;
    if (t){ text = t; isFinal = (msg.type !== 'delta'); }
  } else if (msg.type === 'delta' && msg.delta){
    if (typeof msg.delta === 'string') text = msg.delta;
    else if (msg.delta.content){
      if (Array.isArray(msg.delta.content)){
        const t = msg.delta.content.find(c => (c.type === 'text' && c.text))?.text;
        if (t) text = t;
      } else if (typeof msg.delta.content === 'string') {
        text = msg.delta.content;
      }
    }
    isFinal = false;
  }

  // Non-assistant utility frames
  if (!text && !isFinal){
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
    return;
  }

  // Render/update bubble
  const st = upsertAssistantTurn(turnId, text || '', !!isFinal);

  // Debounced auto-finalize for streaming chunk-only turns
  if (msg.type === 'assistant_chunk' || msg.type === 'assistant_text'){
    // reset per-turn debounce
    clearTimeout(turnDebounce.get(turnId));
    const h = setTimeout(()=>{
      const s = turnState.get(turnId);
      if (s && !s.final){
        s.final = true;
        if (!s.ttsStarted && s.text){
          s.ttsStarted = true;
          speakText(s.text).catch(()=>{});
        }
        setDot('ready');
      }
    }, 500); // finalize after 500ms of no more chunks
    turnDebounce.set(turnId, h);
    setDot('speaking');
  }

  // Explicit final → TTS fallback once
  if (isFinal){
    if (st && !st.ttsStarted && st.text){
      st.ttsStarted = true;
      speakText(st.text).catch(()=>{});
    }
    setDot('ready');
  }
}
