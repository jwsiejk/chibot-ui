// /static/js/app.js — chat helpers only (imports constrained by spec)
import { closeWS } from '/static/js/ws.js?v=v20250911b';
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
    state==='thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
}

function showBanner(msg){
  const b = $('#inlineLoginMsg');
  if (!b) { console.warn('[AskChip]', msg); return; }
  b.textContent = msg;
  b.classList.add('warn');
}

function disableForEnded(){
  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');
  const sendBtns = [$('#composerSend'), $('#sendBtn')].filter(Boolean);

  if (startBtn) startBtn.disabled = false;
  if (endBtn)   endBtn.disabled   = true;
  for (const b of sendBtns) b.disabled = true;

  setDot('ready');
}

/* ---------------- Chat output rendering ---------------- */

// resolve lazily so DOM timing never throws
function chatRoot(){ return document.getElementById('chat'); }
function ensureChatContainer(){
  let c = document.getElementById('chat');
  if (!c){
    c = document.createElement('div');
    c.id = 'chat';
    c.className = 'chat';
    // Try to put it before the composer area
    const composer = document.getElementById('composer');
    if (composer && composer.parentElement) {
      composer.parentElement.parentElement.insertBefore(c, composer.parentElement);
    } else {
      document.body.appendChild(c);
    }
  }
  return c;
}

function appendBubble(role, text){
  let chatEl = chatRoot();
  if (!chatEl) chatEl = ensureChatContainer();
  const div = document.createElement('div');
  div.className = role === 'user' ? 'bubble user' : 'bubble assistant';
  div.textContent = text ?? '';
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

/**
 * Handle frames coming from ws.js → UI
 * Expected shapes (examples):
 *  {type:'assistant_text', text:'...'}
 *  {type:'assistant_final', text:'...'}
 *  {role:'assistant', content:'...'}
 */
export function handleAssistantFrame(frame){
  try {
    const t = frame?.text ?? frame?.content ?? '';
    const role = frame?.role || (frame?.type?.startsWith('assistant') ? 'assistant' : 'other');
    if (t && role === 'assistant') appendBubble('assistant', t);
  } catch(err){
    console.warn('[handleAssistantFrame]', err, frame);
  }
}

/* ---------------- Send / End ---------------- */

let ending = false;

export async function onSend(){
  const input = document.getElementById('composerInput') || document.getElementById('composer');
  const val = (input?.value || '').trim();
  if (!val) return;
  appendBubble('user', val);
  if (input) input.value = '';

  const sid = getSID();

  try {
    await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ type: 'user', text: val, session_id: sid })
    });
  } catch (e){
    console.warn('[onSend] POST /api/v1/chat failed', e);
  }
}

export async function onEnd(){
  if (ending) return;
  ending = true;
  try {
    // Legacy HTTP notify removed — WS is the source of truth for session end.
    // OPTIONAL: if desired, tell server we're closing the stream first:
    // try { sendCloseStream(); } catch {}

    // Close WS and mark client idle
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
