import { openWS, waitWSOpen, isOpen, closeWS, configure } from '/static/js/ws.js?v=v20250911b';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js';
import { initMic } from '/static/js/voice.js';
import { unlockAudio, playStream, stopPlayback } from '/static/js/audio.js';
import { getSID } from '/static/js/util/sid.js';
import * as App from '/static/js/app.js';

// /static/js/bootstrap.js — single owner of Start/End/Send + audio unlock + WS→UI wiring

const $ = (s) => document.querySelector(s);

let startInFlight = false;
let started = false;

// ----- NEW: assistant text de-dupe (turn_id + text) -----
const _assistantSeen = new Set();
function _isAssistantTextFrame(d) {
  const t = d?.type;
  return t === 'assistant_text' || t === 'assistant_chunk' || t === 'assistant_final' || d?.role === 'assistant';
}
function _dedupeAssistant(d) {
  // tolerate servers that send without turn_id (fallback to "na")
  const turnId = d?.turn_id ?? 'na';
  const text   = d?.text ?? d?.delta ?? d?.content ?? '';
  const key    = `${turnId}::${text}`;
  if (!text) return true; // empty texts don't render anyway
  if (_assistantSeen.has(key)) return false;
  _assistantSeen.add(key);
  return true;
}

function setDot(state){
  const dot = $('#stateDot');
  if (!dot) return;
  dot.className = 'dot ' + (
    state === 'listening' ? 'dot-listening' :
    state === 'speaking'  ? 'dot-speaking'  :
    state === 'thinking'  ? 'dot-thinking'  : 'dot-ready'
  );
}

function showBanner(msg){
  const b = $('#inlineLoginMsg');
  if (!b) { console.warn('[AskChip]', msg); return; }
  b.textContent = msg;
  b.classList.add('warn');
}

function wireWSEventsOnce(){
  if (window.__askchip_ws_wired) return;
  window.__askchip_ws_wired = true;

  // Log first few frames to verify payload shape
  let seen = 0;
  window.addEventListener('askchip-ws', (ev) => {
    const d = ev.detail || {};
    const t = d.type || '';

    // Try to play audio chunks if present on known keys
    const audioChunks = d.audio_chunks || d.chunks || d.audio || null;
    const mime = d.mime || 'audio/webm; codecs=opus';
    if (audioChunks && (t === 'assistant_audio' || t === 'AudioChunk' || t === 'TTSChunk' || t === 'audio')) {
      try { playStream(audioChunks, mime); } catch(e){ console.warn('[bootstrap] audio play error', e); }
    }

    if (seen < 5) {
      try { console.log('[WS→UI]', JSON.stringify(d)); } catch {}
      seen++;
    }

    // De-dupe assistant text frames so greet can't double-render
    if (_isAssistantTextFrame(d)) {
      if (!_dedupeAssistant(d)) return; // swallow duplicate
    }

    try { App.handleAssistantFrame(d); } catch (e) { console.warn('App.handleAssistantFrame error', e); }
  });

  window.addEventListener('askchip-ws-close', (ev) => {
    console.warn('[WS close]', ev.detail);
    // Optionally: setDot('ready');
  });
}

async function ensureWsOpenOrFail(timeoutMs = 5000){
  if (isOpen()) return true;
  openWS();
  try {
    await Promise.race([
      waitWSOpen(),
      new Promise((_, rej) => setTimeout(() => rej(new Error('WS timeout')), timeoutMs))
    ]);
  } catch {
    return false;
  }
  return isOpen();
}

async function startOnce(){
  if (started || startInFlight) return;
  startInFlight = true;
  setDot('thinking');

  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');

  try{
    if (startBtn) startBtn.disabled = true;

    // 0) Audio unlock so TTS is permitted by browser autoplay policies
    try { await unlockAudio(); } catch {}

    // 1) Network/CSRF prep
    try { installFetchInterceptor(); } catch {}
    try { await ensureCSRF(); } catch {}

    // 2) WS first — verify actually OPEN or abort
    const ok = await ensureWsOpenOrFail(5000);
    if (!ok){
      showBanner('WebSocket did not open — greet aborted.');
      setDot('ready');
      if (startBtn) startBtn.disabled = false;
      startInFlight = false;
      return;
    }

    // 3) Wire WS → UI events exactly once
    wireWSEventsOnce();

    // 4) Mic permission (best effort)
    try { await initMic(); } catch {}

    // 5) WS-only greet using the SAME session id as WS
    const sid = getSID();
    // Fire the WS Configure greet (no HTTP fetch)
    configure({ greet: true, reset: 1, session_id: sid });

    // Watchdog: if no assistant frames within 6s after sending WS greet, warn
    let gotAssistant = false;
    const markAssistant = (ev) => {
      const d = ev.detail || {};
      if (_isAssistantTextFrame(d)) gotAssistant = true;
    };
    window.addEventListener('askchip-ws', markAssistant, { once: true });

    setTimeout(() => {
      if (!gotAssistant) showBanner('No assistant frames after greet — check WS handler/payload.');
    }, 6000);

    // Ready for user input
    if (endBtn) endBtn.disabled = false;
    const sendBtnA = document.getElementById('composerSend');
    const sendBtnB = document.getElementById('sendBtn');
    if (sendBtnA) sendBtnA.disabled = false;
    if (sendBtnB) sendBtnB.disabled = false;

    // Mark session active so ws.js auto-reconnects through restarts
    window.__askchip_session_started = true;
    started = true;
    setDot('ready');

  } catch (e){
    console.error('[bootstrap] start failed', e);
    if (startBtn) startBtn.disabled = false;
    setDot('ready');
  } finally {
    startInFlight = false;
  }
}

function wireUI(){
  // Safety guard: prevent duplicate UI wiring if module loaded twice
  if (window.__bootstrapWired) {
    console.warn('[bootstrap] duplicate wiring prevented');
    return;
  }
  window.__bootstrapWired = true;

  const startBtn = document.getElementById('startButton');
  const endBtn   = document.getElementById('endButton');
  const sendBtnA = document.getElementById('composerSend');
  const sendBtnB = document.getElementById('sendBtn');
  const form     = document.getElementById('composerForm');
  const composer = document.getElementById('composer');

  if (startBtn) startBtn.addEventListener('click', startOnce);
  if (endBtn)   endBtn.addEventListener('click', App.onEnd);

  // Stop TTS as soon as user interacts to send or type (soft barge-in polish)
  const stopAudio = () => { try { stopPlayback(); } catch {} };

  const bindSend = (btn) => {
    if (!btn) return;
    btn.addEventListener('click', (e) => { stopAudio(); App.onSend(); });
  };
  bindSend(sendBtnA);
  bindSend(sendBtnB);

  if (form) form.addEventListener('submit', (e) => { e.preventDefault(); stopAudio(); App.onSend(); });

  if (composer) {
    composer.addEventListener('keydown', stopAudio);
    composer.addEventListener('input', stopAudio);
  }

  if (sendBtnA) sendBtnA.disabled = true;
  if (sendBtnB) sendBtnB.disabled = true;
  if (endBtn)   endBtn.disabled   = true;

  // Reset bootstrap state when session ends so Start can be used again
  window.addEventListener('askchip-session-ended', () => {
    started = false;
    const sb = $('#startButton');
    if (sb) sb.disabled = false;
    setDot('ready');
  });

  setDot('ready');
  window.__askchip_bootstrap_loaded = true;
  console.log('[AskChip] bootstrap loaded');
}

if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', wireUI);
} else {
  wireUI();
}
