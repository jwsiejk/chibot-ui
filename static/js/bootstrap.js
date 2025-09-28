// bootstrap.js
import { openWS, waitWSOpen, isOpen, closeWS, configure } from '/static/js/ws.js?v=v20250911b';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js';
import { initMic, armVAD, disarmVAD } from '/static/js/voice.js';
import { unlockAudio, stopPlayback } from '/static/js/audio.js';
import { getSID } from '/static/js/util/sid.js';
import * as App from '/static/js/app.js';
import * as Visualizer from '/static/js/visualizer.js';

// /static/js/bootstrap.js — single owner of Start/End/Send + audio unlock + WS→UI wiring

const $ = (s) => document.querySelector(s);

let startInFlight = false;
let started = false;

// ----- Assistant text de-dupe (turn_id + text) -----
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

function setStatusText(text){
  const status = $('#statusText');
  if (!status) return;
  status.textContent = text;
}

function showBanner(msg){
  const b = $('#inlineLoginMsg');
  if (!b) { console.warn('[AskChip]', msg); return; }
  b.textContent = msg;
  b.classList.add('warn');
}

function _disableButtons() {
  const endBtn   = $('#endButton');
  const sendBtnA = $('#composerSend');
  const sendBtnB = $('#sendBtn');
  if (sendBtnA) sendBtnA.disabled = true;
  if (sendBtnB) sendBtnB.disabled = true;
  if (endBtn)   endBtn.disabled   = true;
}

function _enableButtons() {
  const endBtn   = $('#endButton');
  const sendBtnA = $('#composerSend');
  const sendBtnB = $('#sendBtn');
  if (sendBtnA) sendBtnA.disabled = false;
  if (sendBtnB) sendBtnB.disabled = false;
  if (endBtn)   endBtn.disabled   = false;
}

function wireWSEventsOnce(){
  if (window.__askchip_ws_wired) return;
  window.__askchip_ws_wired = true;

  // Log first few frames to verify payload shape
  let seen = 0;
  window.addEventListener('askchip-ws', (ev) => {
    const d = ev.detail || {};
    const t = d.type || '';

    if (seen < 5) {
      try { console.log('[WS→UI]', JSON.stringify(d)); } catch {}
      seen++;
    }

    // Lightweight state dot hints (no UI regressions)
    if (t === 'assistant_audio') setDot('speaking');
    if (t === 'UtteranceEnd')    setDot('ready');
    if (t === 'state' && d.phase) {
      if (d.phase === 'ready')    setDot('ready');
      if (d.phase === 'thinking') setDot('thinking');
      if (d.phase === 'speaking') setDot('speaking');
      if (d.phase === 'listening')setDot('listening');
    }

    // De-dupe assistant text frames so greet can't double-render
    if (_isAssistantTextFrame(d)) {
      if (!_dedupeAssistant(d)) return; // swallow duplicate
    }

    try { App.handleAssistantFrame(d); } catch (e) { console.warn('App.handleAssistantFrame error', e); }
  });

  window.addEventListener('askchip-ws-close', (ev) => {
    const detail = ev.detail || {};
    const normal = detail.code === 1000 || detail.code === 1001;
    const reason = detail.reason || (normal ? 'normal closure' : '');
    const payload = { ...detail, reason };

    if (normal) {
      console.info('[WS close]', payload);
    } else {
      console.warn('[WS close]', payload);
    }
    // When WS closes, disable Send/End and re-enable Start
    _disableButtons();
    const sb = $('#startButton');
    if (sb) sb.disabled = false;
    setDot('ready');
  });
}

function wireVoiceEventsOnce(){
  if (window.__askchip_voice_wired) return;
  window.__askchip_voice_wired = true;

  window.addEventListener('askchip-voice', (ev) => {
    const detail = ev?.detail || {};
    const state = detail.state;
    const label = detail.statusText ?? detail.label ?? detail.message;

    if (state === 'armed'){
      setDot('listening');
      setStatusText(label || 'Listening…');
      return;
    }

    if (state === 'recording'){
      setDot('speaking');
      setStatusText(label || 'Recording…');
      return;
    }

    if (state === 'idle'){
      setDot('ready');
      setStatusText(label || 'Ready');
    }
  });
}

async function ensureWsOpenOrFail(timeoutMs = 5000){
  if (isOpen()) return true;
  try {
    await openWS();
  } catch (err) {
    console.warn('[bootstrap] openWS failed', err);
    return false;
  }
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

    // 1) Network/CSRF prep (kept for compatibility; harmless in WS-only mode)
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

    // 4) WS-only greet using the SAME session id as WS — fire ASAP so text arrives while
    //    audio hardware comes online.
    const sid = getSID();
    try {
      configure({ greet: true, reset: 1, session_id: sid });
    } catch (e) {
      console.warn('[bootstrap] WS configure failed, cannot greet', e);
      showBanner('Greet failed to send — check WS configure()');
      throw e;
    }

    // 5) Mic permission + arm echo-aware VAD (voice-first path)
    let visualizerStream = null;
    try {
      visualizerStream = await Visualizer.start();
    } catch (e) {
      console.warn('[bootstrap] visualizer init failed', e);
    }

    try {
      const stream = await initMic(visualizerStream ?? undefined);
      await armVAD(stream);         // begins voice turns (one blob per user turn)
    } catch (e) {
      console.warn('[bootstrap] mic/VAD init failed', e);
      try { Visualizer.stop({ reset: true }); } catch {}
    }

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
    _enableButtons();

    // Mark session active so ws.js auto-reconnects through restarts
    window.__askchip_session_started = true;
    started = true;
    setDot('ready');

  } catch (e){
    console.error('[bootstrap] start failed', e);
    if (startBtn) startBtn.disabled = false;
    _disableButtons();
    setDot('ready');
    try { Visualizer.stop({ reset: true }); } catch {}
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

  wireVoiceEventsOnce();

  const startBtn = document.getElementById('startButton');
  const endBtn   = document.getElementById('endButton');
  const sendBtnA = document.getElementById('composerSend');
  const sendBtnB = document.getElementById('sendBtn');
  const form     = document.getElementById('composerForm');
  const composer = document.getElementById('composer');

  if (startBtn) startBtn.addEventListener('click', startOnce);
  if (endBtn)   endBtn.addEventListener('click', () => {
    try { disarmVAD(); } catch {}
    try { Visualizer.stop({ reset: true }); } catch {}
    App.onEnd();
  });

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
    composer.addEventListener('keydown', (ev) => {
      stopAudio();
      if (ev.key !== 'Enter') return;
      if (ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
      const disabled = (sendBtnA && sendBtnA.disabled) || (sendBtnB && sendBtnB.disabled);
      if (disabled) return;
      if (!composer.value || !composer.value.trim()) return;
      ev.preventDefault();
      App.onSend();
    });
    composer.addEventListener('input', stopAudio);
  }

  // Disabled until greet succeeds
  _disableButtons();

  // Reset bootstrap state when session ends so Start can be used again
  window.addEventListener('askchip-session-ended', () => {
    try { disarmVAD(); } catch {}
    try { Visualizer.stop({ reset: true }); } catch {}
    started = false;
    const sb = $('#startButton');
    if (sb) sb.disabled = false;
    _disableButtons();
    setDot('ready');
  });

  setDot('ready');
  setStatusText('Ready');
  window.__askchip_bootstrap_loaded = true;
  console.log('[AskChip] bootstrap loaded');
}

if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', wireUI);
} else {
  wireUI();
}

// Debug import (opt-in via localStorage.AskChipDebug='1')
import '/static/js/debug.js?v=v20250911b';
