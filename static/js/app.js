// /static/js/app.js — chat helpers only (imports constrained by spec)
import { closeWS, sendCloseStream, sendJSON } from './ws_module.js';
import { getSID } from '/static/js/util/sid.js';
import { renderSuggestions } from '/static/js/suggestions.js';
import { logIfEnabled } from './util/logging.js';

// /static/js/app.js — side-effect-free chat helpers + WS→UI rendering
// Exports: onEnd, onSend, handleAssistantFrame

const $  = (s)=>document.querySelector(s);

const ASR_TIMEOUT_MS = 10_000;
const ASR_FINALIZATION_TIMEOUT_MS = 2_000;
let _sessionReadyAt = 0;
let _awaitingFirstAsr = false;
let _noAsrTimeout = null;
let _noAsrNotified = false;
let _lastAsrLatencyMs = null;
let _ttsPlaying = false;
const _asrTurnState = {
  activeId: null,
  complete: false,
  utteranceEnded: false,
};
const _asrCompletionWaiters = new Set();

function _getActiveTurnTraceId() {
  try {
    if (typeof window === 'undefined') return null;
    const id = window.__askchip_turn_trace_id;
    return id ? String(id) : null;
  } catch {
    return null;
  }
}

function _console(level, ...args) {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
}

function _appLog(level, message, detail = undefined) {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      if (!method) return;
      const traceId = _getActiveTurnTraceId();
      const prefix = traceId ? `[ui][trace:${traceId}]` : '[ui]';
      if (detail === undefined) {
        method.call(console, `${prefix} ${message}`);
        return;
      }
      if (detail && typeof detail === 'object') {
        const payload = traceId && detail.traceId !== traceId ? { ...detail, traceId } : detail;
        method.call(console, `${prefix} ${message}`, payload);
        return;
      }
      if (traceId) {
        method.call(console, `${prefix} ${message}`, detail, `trace:${traceId}`);
        return;
      }
      method.call(console, `${prefix} ${message}`, detail);
    } catch {}
  });
}

try {
  window.addEventListener('chip-tts', (ev) => {
    const detail = ev?.detail || {};
    const playing = String(detail.state || '').toLowerCase() === 'playing';
    _ttsPlaying = playing;
    if (playing) {
      _clearAsrTimeout();
    }
  });
} catch {}

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
  _resetAsrTurnState();
}

function _resetAsrTurnState(){
  _asrTurnState.activeId = null;
  _asrTurnState.complete = false;
  _asrTurnState.utteranceEnded = false;
  _flushAsrCompletionWaiters('reset');
}

function _ensureActiveAsrTurn(turnId){
  if (turnId == null) return;
  if (_asrTurnState.activeId !== turnId){
    _asrTurnState.activeId = turnId;
    _asrTurnState.complete = false;
    _asrTurnState.utteranceEnded = false;
  }
}

function _hasAsrFinalEvent(){
  return !!(_asrTurnState.complete || _asrTurnState.utteranceEnded);
}

function _flushAsrCompletionWaiters(reason){
  if (!_asrCompletionWaiters.size) return;
  const waiters = Array.from(_asrCompletionWaiters);
  _asrCompletionWaiters.clear();
  waiters.forEach((waiter) => {
    if (waiter?.timer) {
      clearTimeout(waiter.timer);
      waiter.timer = null;
    }
    try {
      waiter?.resolve?.(reason);
    } catch {}
  });
}

function _notifyAsrCompletion(reason){
  if (!_hasAsrFinalEvent()) return;
  _flushAsrCompletionWaiters(reason);
}

function _waitForAsrCompletion(timeoutMs){
  if (_hasAsrFinalEvent()) {
    return Promise.resolve('already_final');
  }
  return new Promise((resolve) => {
    const waiter = { resolve, timer: null };
    if (typeof timeoutMs === 'number' && timeoutMs >= 0) {
      waiter.timer = setTimeout(() => {
        if (_asrCompletionWaiters.delete(waiter)) {
          try {
            resolve('timeout');
          } catch {}
        }
      }, timeoutMs);
    }
    _asrCompletionWaiters.add(waiter);
  });
}

function _extractAsrTurnId(frame){
  return (
    frame?.turn_id ??
    frame?.channel?.turn_id ??
    frame?.turnId ??
    frame?.channel?.turnId ??
    null
  );
}

function _isAsrFinalFrame(frame){
  return !!(
    frame?.channel?.is_final ??
    frame?.is_final ??
    frame?.final ??
    false
  );
}

function _scheduleAsrWatchdog(){
  _clearAsrTimeout();
  _awaitingFirstAsr = true;
  _noAsrNotified = false;
  _sessionReadyAt = Date.now();
  _lastAsrLatencyMs = null;
  _noAsrTimeout = setTimeout(()=>{
    if (_awaitingFirstAsr && !_ttsPlaying){
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
  if (_ttsPlaying) return;
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

function _handleSessionEnd(frame){
  const reason = String(frame?.reason || '').toLowerCase();
  const message = reason === 'silence_auto_close'
    ? 'Closing for now—start a new session whenever you’re ready.'
    : 'Session ended. Press Start to begin a new one.';
  _resetAsrTracking();
  disableForEnded();
  try { _clearSuggestions(); } catch {}
  showBanner(message);
  try { window.__askchip_session_started = false; } catch {}
  try { window.dispatchEvent(new CustomEvent('askchip-session-ended')); } catch {}
  try { closeWS(1000, reason || 'session_end'); } catch {}
}

/* ---------------- Rendering of assistant frames ---------------- */

// Accept WS assistant text frames
function _isAssistantTextFrame(d){
  return d && (
    (d.type === 'assistant_chunk' && (d.text || d.delta || d.content)) ||
    (d.type === 'assistant_text' && (d.text || d.delta || d.content)) ||
    (d.type === 'assistant_final' && (d.text || d.delta || d.content))
  );
}

const _userPreviewState = {
  active: false,
  latestTranscript: '',
  lastFinalText: '',
  ignoring: false,
};

function _ensureUserPreviewEl(){
  try{
    let el = document.getElementById('userTranscriptPreview');
    if (el) return el;
    const statusRow = document.querySelector('.status-row');
    if (!statusRow) return null;
    el = document.createElement('span');
    el.id = 'userTranscriptPreview';
    el.className = 'asr-preview';
    el.setAttribute('aria-live', 'polite');
    el.style.display = 'none';
    statusRow.appendChild(el);
    return el;
  }catch{}
  return null;
}

function _setUserPreview(text){
  try{
    const el = _ensureUserPreviewEl();
    if (!el) return;
    const next = text || '';
    el.textContent = next;
    el.style.display = next ? '' : 'none';
  }catch{}
}

function _clearUserPreview(){
  _setUserPreview('');
}

function _commitUserPreview(){
  try{
    const transcript = _cleanText(_userPreviewState.latestTranscript || '');
    _userPreviewState.active = false;
    _userPreviewState.latestTranscript = '';
    _clearUserPreview();
    if (!transcript){
      _userPreviewState.ignoring = true;
      return;
    }

    if (_userPreviewState.lastFinalText === transcript){
      _userPreviewState.ignoring = true;
      return;
    }

    const msgs = $('#chatMessages');
    if (!msgs) return;

    const last = msgs.lastElementChild;
    if (last && last.dataset && last.dataset.role === 'user' && last.textContent === transcript){
      _userPreviewState.lastFinalText = transcript;
      _userPreviewState.ignoring = true;
      return;
    }

    const bubble = document.createElement('div');
    bubble.dataset.role = 'user';
    bubble.className = 'msg user';
    bubble.textContent = transcript;
    msgs.appendChild(bubble);
    _appLog('info', 'user transcript bubble added', {
      chars: transcript.length,
      text: transcript,
    });
    _userPreviewState.lastFinalText = transcript;
    _userPreviewState.ignoring = true;
    try { msgs.scrollTop = msgs.scrollHeight; } catch {}
  }catch{}
}

function _finalizeUserPreview(opts){
  const resetIgnore = !!(opts && opts.resetIgnore);
  if (!_userPreviewState.active && !_userPreviewState.latestTranscript){
    _userPreviewState.active = false;
    if (resetIgnore) _userPreviewState.ignoring = false;
    _clearUserPreview();
    return;
  }
  _commitUserPreview();
  if (resetIgnore) _userPreviewState.ignoring = false;
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

    const isFinal = (
      d?.channel?.is_final ??
      d?.is_final ??
      d?.final ??
      false
    );

    if (!transcript){
      if (isFinal){
        _finalizeUserPreview();
      } else {
        _userPreviewState.latestTranscript = '';
        _userPreviewState.active = true;
        _setUserPreview('');
      }
      return;
    }

    if (_userPreviewState.ignoring){
      if (transcript === _userPreviewState.lastFinalText){
        if (isFinal){
          _finalizeUserPreview();
        }
        return;
      }
      _userPreviewState.ignoring = false;
    }

    _userPreviewState.active = true;
    _userPreviewState.latestTranscript = transcript;
    _setUserPreview(transcript);

    if (isFinal){
      _finalizeUserPreview();
    }
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
  try { onSend(suggestion); } catch (err) { _console('warn', '[suggestion click] send failed', err); }
}

export function handleAssistantFrame(d){
  if (!d) return;
  const t = d.type;
  const typeNorm = typeof t === 'string' ? t.toLowerCase() : '';

  if (t === 'ready') {
    if (!_ttsPlaying) _scheduleAsrWatchdog();
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

  if (t === 'session_end') {
    _handleSessionEnd(d);
    return;
  }

  // Lightweight state hints (non-invasive)
  if (t === 'assistant_audio') setDot('speaking');
  if (t === 'UtteranceEnd') {
    const turnId = _extractAsrTurnId(d);
    if (turnId != null) _ensureActiveAsrTurn(turnId);
    const matchesActive = turnId == null
      ? _asrTurnState.activeId == null
      : _asrTurnState.activeId === turnId;
    if (matchesActive && _asrTurnState.utteranceEnded) {
      return;
    }
    _finalizeUserPreview({ resetIgnore: true });
    setDot('ready');
    if (matchesActive) {
      _asrTurnState.utteranceEnded = true;
      _notifyAsrCompletion('utterance_end');
    }
  }

  if (typeNorm === 'result' || typeNorm === 'results'){
    const turnId = _extractAsrTurnId(d);
    if (turnId != null) _ensureActiveAsrTurn(turnId);
    const matchesActive = turnId == null
      ? _asrTurnState.activeId == null
      : _asrTurnState.activeId === turnId;
    const isFinal = _isAsrFinalFrame(d);
    if (matchesActive && isFinal && _asrTurnState.complete) {
      return;
    }
    _renderUserTranscript(d);
    if (matchesActive && isFinal) {
      _asrTurnState.complete = true;
      _notifyAsrCompletion('final_result');
    }
    return;
  }

  if (t === 'suggestions'){
    const items = Array.isArray(d.items) ? d.items : [];
    let key = '[]';
    try { key = JSON.stringify(items ?? []); } catch { key = '[]'; }
    if (key !== _lastSuggestionsKey){
      _lastSuggestionsKey = key;
      try { renderSuggestions(items, _handleSuggestionClick); } catch (err) {
        _console('warn', '[handleAssistantFrame] renderSuggestions failed', err);
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
      _console('warn', '[onSend] WS send failed', e);
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
    _console('warn', '[onSend] error', e);
  }
}

/* ---------------- End session (graceful) ---------------- */

let ending = false;

export async function onEnd(){
  if (ending) return;
  ending = true;
  try {
    _clearSuggestions();
    const finalFrameWait = _waitForAsrCompletion(ASR_FINALIZATION_TIMEOUT_MS);
    // Politely signal end of stream over WS, then close with code 1000
    try { await sendCloseStream(); } catch {}
    try { await finalFrameWait; } catch {}

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
export const __TEST_ONLY__ = {
  appLog: _appLog,
  console: _console,
};
