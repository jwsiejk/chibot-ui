// /static/js/app.js — chat helpers only (imports constrained by spec)
import { closeWS, sendCloseStream, sendJSON } from './ws_module.js';
import { getSID } from '/static/js/util/sid.js';
import { renderSuggestions } from '/static/js/suggestions.js';

// /static/js/app.js — side-effect-free chat helpers + WS→UI rendering
// Exports: onEnd, onSend, handleAssistantFrame

const $  = (s)=>document.querySelector(s);

const ASR_TIMEOUT_MS = 10_000;
let _sessionReadyAt = 0;
let _awaitingFirstAsr = false;
let _noAsrTimeout = null;
let _noAsrNotified = false;
let _lastAsrLatencyMs = null;

function _clearAsrTimeout(){
  if (_noAsrTimeout){
    clearTimeout(_noAsrTimeout);
    _noAsrTimeout = null;
  }
}

function _resetAsrTracking(){
  _clearAsrTimeout();
  _awaitingFirstAsr = false;
  _noAsrNotified = false;
  _sessionReadyAt = 0;
  _lastAsrLatencyMs = null;
}

function _scheduleAsrWatchdog(){
  _clearAsrTimeout();
  _awaitingFirstAsr = true;
  _noAsrNotified = false;
  _sessionReadyAt = Date.now();
  _lastAsrLatencyMs = null;
  _noAsrTimeout = setTimeout(()=>{
    if (_awaitingFirstAsr){
      _notifyNoAsr('timeout');
    }
  }, ASR_TIMEOUT_MS);
}

function _markAsrFrameSeen(){
  if (!_awaitingFirstAsr) return;
  _awaitingFirstAsr = false;
  _clearAsrTimeout();
  if (_sessionReadyAt){
    _lastAsrLatencyMs = Date.now() - _sessionReadyAt;
    try {
      window.__askchip_last_asr_latency_ms = _lastAsrLatencyMs;
    } catch {}
    try {
      window.dispatchEvent(new CustomEvent('askchip-asr-first-frame', {
        detail: { latency_ms: _lastAsrLatencyMs }
      }));
    } catch {}
  }
}

function _notifyNoAsr(source, detail){
  if (_noAsrNotified) return;
  _noAsrNotified = true;
  _awaitingFirstAsr = false;
  _clearAsrTimeout();
  const message = 'We didn’t detect any speech. Please check your microphone or try again.';
  showBanner(message);
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice-retry-request', {
      detail: { source, payload: detail ?? null }
    }));
  } catch {}
}

/* ---------------- UI helpers ---------------- */

function setDot(state){
  const dot = $('#stateDot'); if (!dot) return;
  const DOT_CLASSES = {
    ready: 'dot-ready',
    listening: 'dot-listening',
    speaking: 'dot-speaking',
    thinking: 'dot-thinking',
  };
  const nextClass = DOT_CLASSES[state] || 'dot-idle';
  dot.className = 'dot ' + nextClass;
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

// Optional safety: strip legacy debug stamps like "[KB:0]" if any slip through
const _KB_TAG_RE = /\s*\[(?:KB|kb)\s*:\s*\d+\]\s*/g;
function _cleanText(s){ return (s||'').replace(_KB_TAG_RE,' ').trim(); }

/* ---------------- Rendering of assistant frames ---------------- */

// Accept WS assistant text frames
function _isAssistantTextFrame(d){
  return d && (
    (d.type === 'assistant_chunk' && (d.text || d.delta || d.content)) ||
    (d.type === 'assistant_text' && (d.text || d.delta || d.content)) ||
    (d.type === 'assistant_final' && (d.text || d.delta || d.content))
  );
}

function _renderUserTranscript(d){
  _markAsrFrameSeen();
  try{
    const transcriptRaw = (
      d?.channel?.alternatives?.[0]?.transcript ??
      d?.alternatives?.[0]?.transcript ??
      ''
    );
    const transcript = _cleanText(transcriptRaw);
    if (!transcript) return;

    const msgs = $('#chatMessages');
    if (!msgs) return;

    let bubble = msgs.lastElementChild;
    const canReuse = (
      bubble && bubble.dataset && bubble.dataset.role === 'user' && bubble.dataset.asr === '1'
    );
    if (!canReuse){
      bubble = document.createElement('div');
      bubble.dataset.role = 'user';
      bubble.dataset.asr = '1';
      bubble.className = 'msg user';
      msgs.appendChild(bubble);
    } else {
      bubble.className = 'msg user';
    }

    bubble.textContent = transcript;

    const isFinal = (
      d?.channel?.is_final ??
      d?.is_final ??
      d?.final ??
      false
    );

    if (isFinal){
      try { delete bubble.dataset.asr; } catch {}
    }

    try { msgs.scrollTop = msgs.scrollHeight; } catch {}
  }catch{}
}

function _dedupeAssistant(d){
  // single-bubble de-dupe; keep only latest revision for same turn
  try{
    const msgs = $('#chatMessages');
    if (!msgs) return true;
    const last = msgs.lastElementChild;

    const raw = (d.text || d.delta || d.content || '');

    const text = _cleanText(raw);
    const turnId = d?.turn_id;
    const hasTurnId = turnId !== undefined && turnId !== null && turnId !== '';

    if (
      hasTurnId &&
      last &&
      last.dataset &&
      last.dataset.role === 'assistant' &&
      last.dataset.turnId === String(turnId)
    ){
      last.className = 'msg assistant';
      last.textContent = text;
      return false; // swallowed
    }
    const li = document.createElement('div');
    li.dataset.role = 'assistant';
    li.dataset.turnId = String(turnId ?? '');
    li.className = 'msg assistant';
    li.textContent = text;
    msgs.appendChild(li);
    try { msgs.scrollTop = msgs.scrollHeight; } catch {}
  }catch{}
  return true;
}

let _lastSuggestionsKey = null;

function _clearSuggestions(){
  const wrap = document.getElementById('suggestions');
  if (!wrap) return;
  try { wrap.innerHTML = ''; } catch {}
  _lastSuggestionsKey = null;
}

function _handleSuggestionClick(text){
  if (!window.__askchip_session_started) return;
  const suggestion = (text || '').trim();
  if (!suggestion) return;
  const input = $('#composer');
  if (input) {
    try { input.value = suggestion; } catch {}
  }
  try { onSend(suggestion); } catch (err) { console.warn('[suggestion click] send failed', err); }
}

export function handleAssistantFrame(d){
  if (!d) return;
  const t = d.type;

  if (t === 'ready') {
    _scheduleAsrWatchdog();
  }

  if (t === 'no_audio_detected') {
    _notifyNoAsr('server', d);
    return;
  }

  // Surface server errors to the UI banner
  if (t === 'Error') {
    const msg = `Error: ${(d.code||'')}${d.message ? ' ' + d.message : ''}`.trim();
    showBanner(msg || 'An error occurred.');
    return;
  }

  // Lightweight state hints (non-invasive)
  if (t === 'assistant_audio') setDot('speaking');
  if (t === 'UtteranceEnd')    setDot('ready');

  if (t === 'Results' && d.nlu === undefined){
    _renderUserTranscript(d);
    return;
  }

  if (t === 'suggestions'){
    const items = Array.isArray(d.items) ? d.items : [];
    let key = '[]';
    try { key = JSON.stringify(items ?? []); } catch { key = '[]'; }
    if (key !== _lastSuggestionsKey){
      _lastSuggestionsKey = key;
      try { renderSuggestions(items, _handleSuggestionClick); } catch (err) {
        console.warn('[handleAssistantFrame] renderSuggestions failed', err);
      }
    }
    return;
  }

  if (_isAssistantTextFrame(d)) {
    if (!_dedupeAssistant(d)) return;
  }
  // (other frame types can be handled here as needed)
}

/* ---------------- Send (WS-only) ---------------- */

export async function onSend(overrideText){
  try{
    if (!window.__askchip_session_started) return;
    const input = $('#composer');
    if (!input && overrideText == null) return;
    const raw = overrideText != null ? overrideText : (input?.value || '');
    const val = (raw || '').trim(); if (!val) return;

    const sid = getSID();

    // Optimistic render of user bubble
    let optimisticBubble = null;
    try{
      const msgs = $('#chatMessages');
      if (msgs){
        const li = document.createElement('div');
        li.dataset.role = 'user';
        li.className = 'msg user';
        li.textContent = val;
        msgs.appendChild(li);
        optimisticBubble = li;
        try { msgs.scrollTop = msgs.scrollHeight; } catch {}
      }
    }catch{}

    // WS send (WS-only turns; replaces legacy HTTP POST)
    const correlation = (crypto.randomUUID?.() ?? (Date.now() + '-' + Math.random()));
    let sendSucceeded = false;
    try {
      // Server expects: type='user_msg' and correlation_user_msg_id
      sendSucceeded = sendJSON({ type: 'user_msg', text: val, correlation_user_msg_id: correlation, session_id: sid });
    } catch (e){
      console.warn('[onSend] WS send failed', e);
      sendSucceeded = false;
    }

    if (!sendSucceeded){
      if (optimisticBubble && optimisticBubble.parentElement){
        try { optimisticBubble.remove(); } catch {}
      }
      showBanner('Message failed to send over WebSocket.');
      return;
    }

    setDot('thinking');

    // Clear composer after send
    if (input){
      try { input.value = ''; } catch {}
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
    _clearSuggestions();
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

try {
  window.addEventListener('askchip-session-ended', () => {
    _clearSuggestions();
    _resetAsrTracking();
  });
} catch {}

/* ---------------- Expose minimal helpers (optional) ---------------- */

// (Intentionally minimal public surface)
