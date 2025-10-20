console.log("[bootstrap] build=2025-10-16T23:15Z");
// bootstrap.js
import { openWS, waitWSOpen, isOpen, closeWS, configure } from './ws_module.js';
import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js';
import { initMic, armVAD, disarmVAD, forceBargeInStart, forceBargeInEnd } from '/static/js/voice.js';
import { unlockAudio, stopPlayback } from '/static/js/audio.js';
import { getSID } from '/static/js/util/sid.js';
import * as App from '/static/js/app.js';
import * as Visualizer from '/static/js/visualizer.js';
import { logIfEnabled } from '/static/js/util/logging.js';
import PolicyBus from '/static/js/voice/policy/PolicyBus.js';
import { ensureInteractionPolicy as ensureInteractionPolicySnapshot } from '/static/js/voice/policy/InteractionPolicy.js';

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

const manualState = {
  button: null,
  featureEnabled: false,
  allowAutoVad: true,
  autoCommitAllowed: false,
  phase: 'ready',
  pointerActive: false,
  keyActive: false,
  sessionActive: false,
};

let _lastMicStream = null;
let _ttsHoldActive = false;
let _ttsActiveTurnId = null;

function _applyInteractionPolicySnapshot(policy) {
  const normalized = ensureInteractionPolicySnapshot(policy || {});
  manualState.featureEnabled = normalized.allow_ptt_barge === true;
  manualState.allowAutoVad = normalized.allow_auto_vad === true;
  manualState.autoCommitAllowed = normalized.auto_commit_when_ready === true;
  _updateManualButtonAvailability();
}

try {
  const existingPolicy = PolicyBus.getPolicy();
  if (existingPolicy) {
    _applyInteractionPolicySnapshot(existingPolicy);
  }
} catch {}

PolicyBus.on('policy', (policy) => {
  try {
    _applyInteractionPolicySnapshot(policy);
  } catch (err) {
    _console('warn', '[bootstrap] policy sync failed', err);
  }
});

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

  const btn = manualState.button;
  if (!btn || btn.disabled || btn.hidden || !manualState.sessionActive) {
    return;
  }

  if (ev?.type === 'mousedown') {
    if (typeof ev.button === 'number' && ev.button !== 0) return;
    if (typeof ev.buttons === 'number' && (ev.buttons & 1) === 0) return;
  }

  if (ev?.type === 'touchstart' && ev?.touches && ev.touches.length > 1) {
    return;
  }

  if (btn && ev?.target && !btn.contains(ev.target)) {
    return;
  }

  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (manualState.pointerActive || manualState.keyActive) return;

  manualState.pointerActive = true;
  _updateManualButtonAvailability();

  const started = forceBargeInStart({ source: 'pointer' });
  if (!started) {
    manualState.pointerActive = false;
    _updateManualButtonAvailability();
  }
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

  const btn = manualState.button;
  if (!btn || btn.disabled || btn.hidden || !manualState.sessionActive) {
    return;
  }
  if (ev?.target && ev.target !== btn) {
    return;
  }

  const key = ev?.code || ev?.key || '';
  if (!(key === 'Space' || key === 'Spacebar' || key === ' ')) return;
  if (ev?.repeat) return;
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (manualState.keyActive || manualState.pointerActive) return;

  manualState.keyActive = true;
  _updateManualButtonAvailability();

  const started = forceBargeInStart({ source: 'keyboard' });
  if (!started) {
    manualState.keyActive = false;
    _updateManualButtonAvailability();
  }
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

function _handleMicVadFailure(err, { startBtn = null, endBtn = null } = {}) {
  try { _console('warn', '[bootstrap] mic/VAD init failed', err); } catch {}
  showBanner('Microphone unavailable — voice capture disabled.');
  setStatusText('Voice capture unavailable');
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice', {
      detail: { state: 'idle', label: 'Voice capture unavailable' }
    }));
  } catch {}
  try { Visualizer.stop({ reset: true }); } catch {}
  _disableButtons();
  const resolvedStartBtn = startBtn || $('#startButton');
  const resolvedEndBtn   = endBtn   || $('#endButton');
  if (resolvedEndBtn) resolvedEndBtn.disabled = true;
  if (resolvedStartBtn) resolvedStartBtn.disabled = false;
  setDot('ready');
  started = false;
  try { window.__askchip_session_started = false; } catch {}
  try { closeWS(1000, 'mic_unavailable'); } catch {}
  startInFlight = false;
}

function _ensureVoiceCtxLoaded() {
  if (typeof window === 'undefined') return;
  if (window.__askchip_voice_ctx != null) return;
  import('./voice/runtime/AdaptiveRuntime.js')
    .then((mod) => {
      const ctx = mod?.__TEST_ONLY__?.getCtx?.();
      if (ctx) window.__askchip_voice_ctx = ctx;
    })
    .catch(() => {});
}

function wireWSEventsOnce(){
  if (window.__askchip_ws_wired) return;
  window.__askchip_ws_wired = true;

  const normalizeCloseCode = (detail = {}) => {
    if (!detail || typeof detail !== 'object') return null;
    const raw = detail.code;
    if (Number.isFinite(raw)) return raw;
    if (typeof raw === 'string' && raw.trim()) {
      const parsed = Number.parseInt(raw, 10);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  };

  const describeWsClose = (detail = {}) => {
    const reason = typeof detail?.reason === 'string' ? detail.reason.trim() : '';
    if (reason) return reason;
    if (detail?.wasClean === true) return 'clean close';
    const code = normalizeCloseCode(detail);
    if (code === 1000 || code === 1001) return 'normal closure';
    if (code === 1006) return 'network disconnect';
    if (code === 1012) return 'service restart';
    if (code === 1013) return 'service overload';
    if (code === 1014) return 'bad gateway';
    if (code === 1015) return 'TLS handshake failure';
    return code != null ? `code ${code}` : 'unknown reason';
  };

  const isExpectedAbnormalClose = (detail = {}) => {
    if (!detail || typeof detail !== 'object') return false;
    if (detail.wasClean === true) return true;
    if (detail.reconnectScheduled) return true;
    const code = normalizeCloseCode(detail);
    if (code === 1006) return true;
    if (code === 1012 || code === 1013) return true;
    return false;
  };

  // Log first few frames to verify payload shape
  let seen = 0;
  const loggedTypes = new Set();
  logWsListenerAttach('askchip-ws', 'bootstrap-wire');
  window.addEventListener('askchip-ws', (ev) => {
    const d = ev.detail || {};
    const t = d.type || '';
    const normalizedType = typeof t === 'string' ? t : '';
    const frameTurnId = d && d.turn_id != null ? String(d.turn_id) : null;

    if (normalizedType === 'TTS_START' || normalizedType === 'assistant_audio' || normalizedType === 'tts:start') {
      if (!_ttsHoldActive) {
        _ttsHoldActive = true;
        _ttsActiveTurnId = frameTurnId;
        try {
          disarmVAD();
          _console('log', '[bootstrap] VAD disarmed while TTS is active');
        } catch (err) {
          try { _console('warn', '[bootstrap] VAD disarm failed during TTS', err); } catch {}
        }
      } else if (!_ttsActiveTurnId && frameTurnId) {
        _ttsActiveTurnId = frameTurnId;
      }
    }

    if (normalizedType === 'UtteranceEnd' || normalizedType === 'utteranceend') {
      const idsMatch =
        !_ttsActiveTurnId || !frameTurnId || String(frameTurnId) === String(_ttsActiveTurnId);
      if (_ttsHoldActive && idsMatch) {
        _ttsHoldActive = false;
        _ttsActiveTurnId = null;
      }
    }

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
    const code = normalizeCloseCode(detail);
    const normal = code === 1000 || code === 1001;
    const expectedAbnormal = !normal && isExpectedAbnormalClose(detail);
    const reason = describeWsClose(detail);
    const payload = { ...detail, code, reason };

    const logLevel = normal || expectedAbnormal ? 'info' : 'warn';
    _console(logLevel, '[WS close]', payload);
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
    const name = detail.name || detail.event;
    if (name === 'barge_in') {
      const reason = typeof detail.reason === 'string' ? detail.reason.toLowerCase() : '';
      const manual = detail.manual === true || reason === 'manual';
      if (_ttsHoldActive && !manual) {
        try { _console('log', '[bootstrap] suppressed local barge_in during TTS'); } catch {}
        return;
      }
    }    
    if (name === 'turn_open' || name === 'turn_close') {
      _console('info', `askchip-voice ${name}
    if (name === 'turn_open') {
      try { window.__askchip_has_opened_turn = true; window.__askchip_turn_open = true; } catch {}
    }
    if (name === 'turn_close') {
      try { window.__askchip_turn_open = false; } catch {}
    }
`, {
        ts_ms: detail.ts_ms ?? detail.ts ?? Date.now(),
        session_id: detail.sessionId || detail.session_id || null,
        turn_id: detail.turnId ?? detail.turn_id ?? null,
        reason: detail.reason || undefined,
      });
    }
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
    const manualFeatureEnabled = manualState.featureEnabled;
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
      const manualMode = !manualState.allowAutoVad;
      const autoCommit = manualState.autoCommitAllowed;
      _console('log', '[bootstrap] startOnce sending greet configure');
      configure({
        greet: true,
        reset: 1,
        session_id: sid,
        // Surface the runtime toggles so diagnostics can confirm policy.
        feature_manual_barge_in: manualFeatureEnabled,
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
      _lastMicStream = stream ?? null;
      _console('log', '[bootstrap] startOnce mic initialized — arming VAD for voice turns');
      await armVAD(stream ?? undefined);
      _console('log', '[bootstrap] startOnce VAD armed — recorder priming should now observe greet flow state');
      _ensureVoiceCtxLoaded();
    } catch (e) {
      _handleMicVadFailure(e, { startBtn, endBtn });
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
    manualState.button.hidden = !manualState.featureEnabled;
    manualState.button.disabled = true;
    const dataset = manualState.button.dataset || {};
    if (!dataset.askchipManualListenersAttached) {
      manualState.button.addEventListener('mousedown', _manualPointerDown);
      manualState.button.addEventListener('mouseup', _manualPointerUp);
      manualState.button.addEventListener('mouseleave', _manualPointerUp);
      manualState.button.addEventListener('touchstart', _manualPointerDown, { passive: false });
      manualState.button.addEventListener('touchend', _manualPointerUp);
      manualState.button.addEventListener('touchcancel', (ev) => _manualPointerUp(ev));
      manualState.button.addEventListener('keydown', _manualKeyDown);
      manualState.button.addEventListener('keyup', _manualKeyUp);
      try {
        manualState.button.dataset.askchipManualListenersAttached = '1';
      } catch {}
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
