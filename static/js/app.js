// /static/js/app.js — chat helpers only (imports constrained by spec)
import { closeWS, sendCloseStream, sendJSON } from '/static/js/ws.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';

// /static/js/app.js — side-effect-free chat helpers + WS→UI rendering
// Exports: onEnd, onSend, handleAssistantFrame

const $  = (s)=>document.querySelector(s);

/* ---------------- UI helpers ---------------- */

function setDot(state){
  const dot = $('#stateDot'); if (!dot) return;
  dot.className = 'dot ' + (
    state==='listening' ? 'dot-listening' :
    state==='speaking'  ? 'dot-speaking'  :
    state==='ready'     ? 'dot-ready'     :
    'dot-idle'
  );
}

function showBanner(msg){ const el=$('#statusText'); if(el) el.textContent = msg || ''; }
function disableForEnded(){
  const input = $('#composer');
  const sendA = $('#composerSend');
  const sendB = $('#sendBtn');
  const endBtn= $('#endButton');
  if (input) input.value = '';
  [sendA, sendB].forEach(b=>{ if(b) b.disabled = true; });
  if (endBtn) endBtn.disabled = true;
  setDot('ready');
}

/* ---------------- Rendering of assistant frames ---------------- */

function _isAssistantTextFrame(d){
  return d && d.type === 'Results' && d.nlu === undefined && d.alternatives && d.alternatives[0]?.transcript;
}

function _dedupeAssistant(d){
  // single-bubble de-dupe; keep only latest revision for same turn
  try{
    const msgs = $('#chatMessages');
    if (!msgs) return true;
    const last = msgs.lastElementChild;
    const text = d.alternatives[0]?.transcript || '';
    if (last && last.dataset && last.dataset.role === 'assistant'){
      last.textContent = text;
      return false; // swallowed
    }
    const li = document.createElement('div');
    li.dataset.role = 'assistant';
    li.textContent = text;
    msgs.appendChild(li);
  }catch{}
  return true;
}

export function handleAssistantFrame(d){
  if (!d) return;
  if (_isAssistantTextFrame(d)) {
    if (!_dedupeAssistant(d)) return;
  }
  // (other frame types can be handled here as needed)
}

/* ---------------- Send (WS-only) ---------------- */

export async function onSend(){
  try{
    const input = $('#composer'); if (!input) return;
    const val = (input.value || '').trim(); if (!val) return;
    const sid = getSID();

    // Optimistic render of user bubble
    try{
      const msgs = $('#chatMessages');
      if (msgs){
        const li = document.createElement('div');
        li.dataset.role = 'user';
        li.textContent = val;
        msgs.appendChild(li);
      }
    }catch{}

    // WS send (replaces legacy HTTP POST)
    const userMsgId = (crypto.randomUUID?.() ?? (Date.now() + '-' + Math.random()));
    try {
      sendJSON({ type: 'User', text: val, session_id: sid, userMsgId });
    } catch (e){
      console.warn('[onSend] WS send failed', e);
    }
  } catch (e){
    console.warn('[onSend] error', e);
  }
}

/* ---------------- End session (graceful) ---------------- */

let ending = false;

export async function onEnd(){
  if (ending) return;
  ending = true;
  try {
    // Politely signal end of stream over WS, then close with code 1000
    try { sendCloseStream(); } catch {}
    await new Promise(r => setTimeout(r, 100));

    try { closeWS(1000, 'user_end'); } catch {}

    // Rotate the session so the next Start is clean (correct key)
    try { localStorage.removeItem('chip.sid'); } catch {}

    // Notify others (bootstrap etc.)
    try { window.dispatchEvent(new CustomEvent('askchip-session-ended')); } catch {}

    // Update UI
    disableForEnded();
    showBanner('Session ended. Press Start to begin a new one.');

    // Clear any bootstrap “session started” latch
    window.__askchip_session_started = false;

  } finally {
    ending = false;
  }
}

/* ---------------- Expose minimal helpers (optional) ---------------- */

// (Intentionally minimal public surface)
