// bootstrap.js
import { openWS, waitWSOpen, isOpen, closeWS, configure } from './ws_module.js';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js';
import { initMic, armVAD, disarmVAD, forceBargeInStart, forceBargeInEnd, setGreetGateActive } from '/static/js/voice.js';
import { unlockAudio, stopPlayback } from '/static/js/audio.js';
import { getSID } from '/static/js/util/sid.js';
import * as App from '/static/js/app.js';
import * as Visualizer from '/static/js/visualizer.js';
import { logIfEnabled } from '/static/js/util/logging.js';

// /static/js/bootstrap.js — single owner of Start/End/Send + audio unlock + WS→UI wiring

const $ = (s) => document.querySelector(s);

let startInFlight = false;
let started = false;
let startOnceCallCount = 0;
let wsListenerSeq = 0;

function logWsListenerAttach(eventName, note){
  const id = `${eventName}#${++wsListenerSeq}`;
  try {
    _console('info', '[bootstrap] ws listener attached', { listener_id: id, event: eventName, note });
  } catch {}
  return id;
}

function _console(level, ...args) {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
}

function _cfgValue(key, fallback) {
  try {
    const cfg = window.__askchip_config || {};
    if (key in cfg) return cfg[key];
    if (cfg.features && key in cfg.features) return cfg.features[key];
  } catch {}
  return fallback;
}

const manualState = {
  button: null,
  featureEnabled: !!_cfgValue('feature_manual_barge_in', true),
  phase: 'ready',
  pointerActive: false,
  keyActive: false,
  sessionActive: false,
};

function _updateManualButtonAvailability() {
  const btn = manualState.button;
  if (!btn) return;
  if (!manualState.featureEnabled) {
    btn.hidden = true;
    btn.disabled = true;
    return;
  }
  btn.hidden = false;
  const shouldEnable = manualState.sessionActive
    && !manualState.pointerActive
    && !manualState.keyActive;
  btn.disabled = !shouldEnable;
}

function _setManualSessionActive(active) {
  manualState.sessionActive = !!active;
  _updateManualButtonAvailability();
}

function _manualPhaseChanged(phase) {
  manualState.phase = phase || 'ready';
  _updateManualButtonAvailability();
}

function _manualPointerDown(ev) {
  if (!manualState.featureEnabled) return;
  if (!ev?.isTrusted) return;  // ← block synthetic/programmatic starts
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (manualState.pointerActive || manualState.keyActive) return;
  manualState.pointerActive = true;
  _updateManualButtonAvailability();
  forceBargeInStart({ source: 'pointer' });
}

function _manualPointerUp(ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!manualState.pointerActive) return;
  manualState.pointerActive = false;
  _updateManualButtonAvailability();
  if (!manualState.keyActive) {
    forceBargeInEnd({ reason: 'pointer_release' });
  }
}

function _manualKeyDown(ev) {
  if (!manualState.featureEnabled) return;
  if (!ev?.isTrusted) return;  // ← block synthetic/programmatic starts
  const key = ev?.code || ev?.key || '';
  if (!(key === 'Space' || key === 'Spacebar' || key === ' ')) return;
  if (ev?.repeat) return;
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (manualState.keyActive || manualState.pointerActive) return;
  manualState.keyActive = true;
  _updateManualButtonAvailability();
  forceBargeInStart({ source: 'keyboard' });
}

function _manualKeyUp(ev) {
  const key = ev?.code || ev?.key || '';
  if (!(key === 'Space' || key === 'Spacebar' || key === ' ')) return;
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!manualState.keyActive) return;
  manualState.keyActive = false;
  _updateManualButtonAvailability();
  if (!manualState.pointerActive) {
    forceBargeInEnd({ reason: 'keyboard_release' });
  }
}

function _manualGlobalCancel(reason = 'global_cancel') {
  if (!manualState.pointerActive && !manualState.keyActive) return;
  manualState.pointerActive = false;
  manualState.keyActive = false;
  _updateManualButtonAvailability();
  forceBargeInEnd({ reason });
}

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
  if (!b) { _console('warn', '[AskChip]', msg); return; }
  b.textContent = msg;
  b.classList.add('warn');
}

function _disableButtons() {
  if (manualState.pointerActive || manualState.keyActive) {
    _manualGlobalCancel('disable_buttons');
  }
  const endBtn   = $('#endButton');
  const sendBtnA = $('#composerSend');
  const sendBtnB = $('#sendBtn');
  if (sendBtnA) sendBtnA.disabled = true;
  if (sendBtnB) sendBtnB.disabled = true;
  if (endBtn)   endBtn.disabled   = true;
  manualState.pointerActive = false;
  manualState.keyActive = false;
  _setManualSessionActive(false);
  manualState.phase = 'ready';
  _updateManualButtonAvailability();
}

function _enableButtons() {
  const endBtn   = $('#endButton');
  const sendBtnA = $('#composerSend');
  const sendBtnB = $('#sendBtn');
  if (sendBtnA) sendBtnA.disabled = false;
  if (sendBtnB) sendBtnB.disabled = false;
  if (endBtn)   endBtn.disabled   = false;
  _setManualSessionActive(true);
}

function wireWSEventsOnce(){
  if (window.__askchip_ws_wired) return;
  window.__askchip_ws_wired = true;

  // Log first few frames to verify payload shape
  let seen = 0;
  const loggedTypes = new Set();
  logWsListenerAttach('askchip-ws', 'bootstrap-wire');
  window.addEventListener('askchip-ws', (ev) => {
    const d = ev.detail || {};
    const t = d.type || '';

    if (seen < 5 || (!loggedTypes.has(t) && loggedTypes.size < 12)) {
      try { _console('log', '[WS→UI]', JSON.stringify(d)); } catch {}
      seen++;
      if (t) loggedTypes.add(t);
    }

    // Lightweight state dot hints (no UI regressions)
    if (t === 'assistant_audio') setDot('speaking');
    if (t === 'assistant_audio') _manualPhaseChanged('speaking');
    if (t === 'UtteranceEnd')    setDot('ready');
    if (t === 'UtteranceEnd')    _manualPhaseChanged('ready');
    if (t === 'ready')           _manualPhaseChanged('ready');
    if (t === 'state' && d.phase) {
      if (d.phase === 'ready')    setDot('ready');
      if (d.phase === 'thinking') setDot('thinking');
      if (d.phase === 'speaking') setDot('speaking');
      if (d.phase === 'listening')setDot('listening');
      _manualPhaseChanged(String(d.phase));
    }

    // De-dupe assistant text frames so greet can't double-render
    if (_isAssistantTextFrame(d)) {
      if (!_dedupeAssistant(d)) return; // swallow duplicate
    }

    try { App.handleAssistantFrame(d); } catch (e) { _console('warn', 'App.handleAssistantFrame error', e); }
  });

  logWsListenerAttach('askchip-ws-close', 'bootstrap-wire');
  window.addEventListener('askchip-ws-close', (ev) => {
    const detail = ev.detail || {};
    const normal = detail.code === 1000 || detail.code === 1001;
    const reason = detail.reason || (normal ? 'normal closure' : '');
    const payload = { ...detail, reason };

    if (normal) {
      _console('info', '[WS close]', payload);
    } else {
      _console('warn', '[WS close]', payload);
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
    _console('warn', '[bootstrap] openWS failed', err);
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
  if (started || startInFlight) {
    try {
      _console('warn', '[bootstrap] startOnce duplicate guard hit', { started, startInFlight });
    } catch {}
    return;
  }

  const attempt = ++startOnceCallCount;
  try {
    _console('info', `[bootstrap] startOnce called N=${attempt}`);
  } catch {}
  startInFlight = true;
  setDot('thinking');

  const startBtn = $('#startButton');
  const endBtn   = $('#endButton');

  try{
    manualState.featureEnabled = !!_cfgValue('feature_manual_barge_in', true);
    _updateManualButtonAvailability();
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
      const manualMode = !!_cfgValue('barge_in_mode_manual', true);
      const autoCommit = !!_cfgValue('auto_commit_when_ready', true);
      _console('log', '[bootstrap] startOnce sending greet configure');
      configure({
        greet: true,
        reset: 1,
        session_id: sid,
        feature_manual_barge_in: manualState.featureEnabled,
        barge_in_mode_manual: manualMode,
        auto_commit_when_ready: autoCommit,
      });
      _console('log', '[bootstrap] startOnce greet configure sent — recorder setup will follow');
      try {
        window.dispatchEvent(new CustomEvent('chip-tts', {
          detail: { state: 'playing', prime: true }
        }));
      } catch {}
      // ^^ This “primes” holdoff if your server’s first audio event is delayed;
      // when real assistant_audio arrives, your existing listener will extend the hold.
    } catch (e) {
      _console('warn', '[bootstrap] WS configure failed, cannot greet', e);
      showBanner('Greet failed to send — check WS configure()');
      throw e;
    }

    // 5) Mic permission + arm echo-aware VAD (voice-first path)
    let visualizerStream = null;
    try {
      visualizerStream = await Visualizer.start();
    } catch (e) {
      _console('warn', '[bootstrap] visualizer init failed', e);
    }

    try {
      const stream = await initMic(visualizerStream ?? undefined);
      _console('log', '[bootstrap] startOnce mic initialized — about to arm VAD for voice turns');
      await armVAD(stream);         // begins voice turns (one blob per user turn)
      _console('log', '[bootstrap] startOnce VAD armed — recorder priming should now observe greet flow state');
    } catch (e) {
      _console('warn', '[bootstrap] mic/VAD init failed', e);
      showBanner('Microphone unavailable — voice capture disabled.');
      setStatusText('Voice capture unavailable');
      try {
        window.dispatchEvent(new CustomEvent('askchip-voice', {
          detail: { state: 'idle', label: 'Voice capture unavailable' }
        }));
      } catch {}
      try { Visualizer.stop({ reset: true }); } catch {}
      _disableButtons();
      if (endBtn) endBtn.disabled = true;
      if (startBtn) startBtn.disabled = false;
      setDot('ready');
      started = false;
      try { window.__askchip_session_started = false; } catch {}
      try { closeWS(1000, 'mic_unavailable'); } catch {}
      startInFlight = false;
      return;
    }

    // Watchdog: if no assistant frames within 6s after sending WS greet, warn
    let gotAssistant = false;
    let markAssistantRemove = false;
    const markAssistant = (ev) => {
      const d = ev.detail || {};
      if (_isAssistantTextFrame(d)) gotAssistant = true;
      if (markAssistantRemove) {
        markAssistantRemove = false;
        try { window.removeEventListener('askchip-ws', markAssistant); } catch {}
      }
    };
    try {
      logWsListenerAttach('askchip-ws', 'bootstrap-markAssistant-once');
      window.addEventListener('askchip-ws', markAssistant, { once: true });
    } catch (err) {
      markAssistantRemove = true;
      logWsListenerAttach('askchip-ws', 'bootstrap-markAssistant-fallback');
      window.addEventListener('askchip-ws', markAssistant);
      try { _console('warn', '[bootstrap] once listener fallback', err); } catch {}
    }

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
    _console('error', '[bootstrap] start failed', e);
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
    _console('warn', '[bootstrap] duplicate wiring prevented');
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
  manualState.button = document.getElementById('pttButton');

  if (manualState.button) {
    if (!manualState.featureEnabled) {
      manualState.button.hidden = true;
      manualState.button.disabled = true;
    } else {
      manualState.button.hidden = false;
      manualState.button.disabled = true;
      manualState.button.addEventListener('mousedown', _manualPointerDown);
      manualState.button.addEventListener('mouseup', _manualPointerUp);
      manualState.button.addEventListener('mouseleave', _manualPointerUp);
      manualState.button.addEventListener('touchstart', _manualPointerDown, { passive: false });
      manualState.button.addEventListener('touchend', _manualPointerUp);
      manualState.button.addEventListener('touchcancel', (ev) => _manualPointerUp(ev));
      manualState.button.addEventListener('keydown', _manualKeyDown);
      manualState.button.addEventListener('keyup', _manualKeyUp);
    }
  }

  window.addEventListener('mouseup', (ev) => { if (manualState.pointerActive) _manualPointerUp(ev); }, { passive: false });
  window.addEventListener('touchend', (ev) => { if (manualState.pointerActive) _manualPointerUp(ev); }, { passive: false });
  window.addEventListener('touchcancel', (ev) => { if (manualState.pointerActive) _manualPointerUp(ev); }, { passive: false });
  window.addEventListener('keyup', _manualKeyUp, { passive: false });
  window.addEventListener('blur', () => _manualGlobalCancel('window_blur'));
  _updateManualButtonAvailability();

  if (startBtn) startBtn.addEventListener('click', startOnce);
  if (typeof window.startCall !== 'function' || window.startCall.__askchipPlaceholder) {
    window.startCall = () => startOnce();
  }
  if (endBtn)   endBtn.addEventListener('click', () => {
    try { disarmVAD(); } catch {}
    try { Visualizer.stop({ reset: true }); } catch {}
    _manualGlobalCancel('end_button');
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
  _console('log', '[AskChip] bootstrap loaded');
}

if (document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', wireUI);
} else {
  wireUI();
}

// Debug import (opt-in via localStorage.AskChipDebug='1')
const __assetVersion =
  (typeof globalThis !== 'undefined' && globalThis.__askchipAssetVersion) || '';
const __debugSuffix = __assetVersion ? `?v=${__assetVersion}` : '';
import(`/static/js/debug.js${__debugSuffix}`);

export const __TEST_ONLY__ = {
  console: _console,
};
