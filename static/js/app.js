// /static/js/app.js — side-effect-free chat helpers + WS→UI rendering
// Exports: onEnd, onSend, handleAssistantFrame
import { openWS, waitWSOpen, closeWS } from '/static/js/ws.js?v=v20250911b';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js?v=v20250911b';
import { initMic } from '/static/js/voice.js?v=v20250911b';
import { getSID } from '/static/js/util/sid.js';

const $  = (s)=>document.querySelector(s);
const $$ = (s)=>Array.from(document.querySelectorAll(s));

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

function enableAfterStart(){
  const endBtn  = $('#endButton');
  const sendBtn = $('#composerSend');
  if (endBtn)  endBtn.disabled  = false;
  if (sendBtn) sendBtn.disabled = false;
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

const chatEl = $('#chat'); // optional; render only if present

function appendBubble(role, text){
  if (!chatEl) return;
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
  const input = $('#composerInput') || $('#composer');
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
    const sid = getSID();

    // Best-effort tell server we’re ending this session
    try {
      await fetch('/api/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ type: 'end', session_id: sid, reason: 'user_end' })
      });
    } catch(e){
      console.debug('[onEnd] notify server failed (continuing):', e);
    }

    // Close WS and mark client idle
    try { closeWS(1000, 'user_end'); } catch {}

    // Rotate the session so the next Start is clean
    try { localStorage.removeItem('askchipSessionId'); } catch {}

    // 👉 Notify bootstrap (and anyone else) that the session ended
    window.dispatchEvent(new CustomEvent('askchip-session-ended'));

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
export { openWS, waitWSOpen };
