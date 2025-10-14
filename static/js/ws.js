// static/js/ws.js — Phase 4/5 hardened: single socket + reconnection + helpers

// === E2E/Prod Observability: WS lifecycle hooks (safe) ===
(function __installWsLifecycleHooks(){
  try {
    const _add = WebSocket.prototype.addEventListener;
    if (_add.__askchip_lifecycle_patched) return;
    WebSocket.prototype.addEventListener = function(ev, fn){
      if (ev === 'close') {
        const wrap = (e) => {
          try { console.info(`[ws] close code=${e.code} clean=${!!e.wasClean} reason=${e.reason||''}`); } catch {}
          try { return fn(e); } catch {}
        };
        return _add.call(this, ev, wrap);
      }
      if (ev === 'error') {
        const wrap = (e) => { try { console.info('[ws] error'); } catch {} try { return fn(e); } catch {} };
        return _add.call(this, ev, wrap);
      }
      return _add.call(this, ev, fn);
    };
    WebSocket.prototype.addEventListener.__askchip_lifecycle_patched = true;
  } catch {}
})();

// Page-exit breadcrumbs (to distinguish user navigation vs. programmatic close)
(function __installPageExitBreadcrumbs(){
  try {
    if (window.__askchip_exit_hooks_installed) return;
    window.addEventListener('beforeunload', () => { try { console.info('[ui] beforeunload'); } catch {} });
    document.addEventListener('visibilitychange', () => {
      try { console.info(`[ui] visibility=${document.visibilityState}`); } catch {}
    });
    window.__askchip_exit_hooks_installed = true;
  } catch {}
})();


// === E2E minimal WS message hook (always-on, safe) ===
(function __installE2EHook(){
  try {
    const _add = WebSocket.prototype.addEventListener;
    if (!_add.__askchip_e2e_patched) {
      WebSocket.prototype.addEventListener = function(ev, fn){
        if (ev === 'message') {
          const wrapper = (e) => {
            try {
              if (typeof e.data === 'string' && e.data.length < 4096) {
                try {
                  const d = JSON.parse(e.data);
                  if (d && typeof d === 'object') {
                    if (d.type === 'assistant_end') { try { console.info('assistant_end'); } catch {} }
                    if (d.type === 'UtteranceEnd')  { try { console.info('UtteranceEnd'); } catch {} }
                    if (d.type === 'latency_breakdown' || d.event === 'latency_breakdown') { try { console.info('latency_breakdown'); } catch {} }
                    if (d.type === 'policy_decision' || (d.event && String(d.event).includes('policy_decision'))) {
                      const val = d.detail || d.decision || (d.payload && d.payload.decision) || 'unknown';
                      try { console.info('policy_decision: ' + String(val)); } catch {}
                    }
                  }
                } catch {}
              }
            } catch {}
            try { return fn(e); } catch {}
          };
          return _add.call(this, ev, wrapper);
        }
        return _add.call(this, ev, fn);
      };
      WebSocket.prototype.addEventListener.__askchip_e2e_patched = true;
    }
  } catch {}
})();

// Responsibilities:
//   (Patched) Optional reconnect control for diagnostics
let __WS_RECONNECT_ENABLED = true;
//   • Own a single WebSocket connection per tab to /ws/v1/chat
//   • Subprotocol auth (bearer + bearer.<token>)
//   • Auto-reconnect with bounded backoff (only if session is active)
//   • Helpers: waitWSOpen(), isOpen(), bufferedAmount(), sendJSON/audio, configure()
//   • DOM events: 'askchip-ws' (messages), 'askchip-ws-close' (terminal close)

import { playStream, audioEnd, unlockAudio } from './audio.js';
import { getSID } from './util/sid.js';
import { logIfEnabled } from './util/logging.js';

let _ws = null;
let _onOpen = [];
let _lastConfigureSid = null;
let _greetSeqCounter = 0;
const _KEEPALIVE_POLL_MS = 1000;
const _KEEPALIVE_IDLE_THRESHOLD_MS = 3500;
const _KEEPALIVE_RESUME_AFTER_UPLOAD_MS = 750;
const _KEEPALIVE_PROVIDER_FALLBACK_MS = 10000;

const _KEEPALIVE_REASON_PROVIDER = 'provider_wait';
const _KEEPALIVE_REASON_UPLOAD = 'audio_upload';

const _keepaliveState = {
  timer: null,
  lastSentTs: 0,
  lastReadyAt: 0,
  providerReady: false,
  pauseReasons: new Set(),
  resumeTimers: new Map(),
  providerGateTimer: null,
  sawFirstDeepgram: false,
};

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

function _wsLog(level, message, detail = undefined) {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      if (!method) return;
      const traceId = _getActiveTurnTraceId();
      const prefix = traceId ? `[ws][trace:${traceId}]` : '[ws]';
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

function _scheduleProviderFallback(){
  if (_keepaliveState.providerGateTimer){
    clearTimeout(_keepaliveState.providerGateTimer);
  }
  if (!_keepaliveState.pauseReasons.has(_KEEPALIVE_REASON_PROVIDER)){
    _pauseKeepalive(_KEEPALIVE_REASON_PROVIDER);
  }
  _keepaliveState.providerGateTimer = setTimeout(()=>{
    _keepaliveState.providerGateTimer = null;
    if (!_keepaliveState.providerReady){
      _keepaliveState.providerReady = true;
    }
    _resumeKeepalive(_KEEPALIVE_REASON_PROVIDER);
  }, _KEEPALIVE_PROVIDER_FALLBACK_MS);
}

let _lastUserSendTs = 0;
let _gotReady = false;
let _openPromise = null;

// reconnect state
let _reconnecting = false;
let _reconnectTimer = null;
let _backoff = 500;            // start 0.5s
const _BACKOFF_MAX = 8000;     // cap 8s
const _BACKOFF_RESET_MS = 30000;

export function isOpen(){ return !!(_ws && _ws.readyState === WebSocket.OPEN); }
export function isConnecting(){ return !!(_ws && _ws.readyState === WebSocket.CONNECTING); }
export function waitWSOpen(){
  return new Promise(res => {
    if (isOpen()) return res();
    _onOpen.push(res);
  });
}
function _notifyOpen(){ for (const fn of _onOpen.splice(0)) { try{ fn(); }catch{} } }

export function bufferedAmount(){ return _ws ? _ws.bufferedAmount : 0; }

function _clearKeepaliveTimer(){
  if (_keepaliveState.timer){
    clearInterval(_keepaliveState.timer);
    _keepaliveState.timer = null;
  }
}

function _resetKeepaliveState(scheduleFallback = false){
  _keepaliveState.lastSentTs = 0;
  _keepaliveState.lastReadyAt = 0;
  _keepaliveState.providerReady = false;
  _keepaliveState.sawFirstDeepgram = false;
  for (const t of _keepaliveState.resumeTimers.values()){
    clearTimeout(t);
  }
  _keepaliveState.resumeTimers.clear();
  if (_keepaliveState.providerGateTimer){
    clearTimeout(_keepaliveState.providerGateTimer);
    _keepaliveState.providerGateTimer = null;
  }
  _keepaliveState.pauseReasons.clear();
  if (scheduleFallback){
    _pauseKeepalive(_KEEPALIVE_REASON_PROVIDER);
    _scheduleProviderFallback();
  }
}

function _pauseKeepalive(reason){
  if (!reason) return;
  const timer = _keepaliveState.resumeTimers.get(reason);
  if (timer){
    clearTimeout(timer);
    _keepaliveState.resumeTimers.delete(reason);
  }
  _keepaliveState.pauseReasons.add(reason);
}

function _resumeKeepalive(reason){
  if (!reason) return;
  const timer = _keepaliveState.resumeTimers.get(reason);
  if (timer){
    clearTimeout(timer);
    _keepaliveState.resumeTimers.delete(reason);
  }
  _keepaliveState.pauseReasons.delete(reason);
}

function _scheduleKeepaliveResume(reason, delayMs){
  if (!reason) return;
  const ms = typeof delayMs === 'number' && delayMs >= 0 ? delayMs : _KEEPALIVE_RESUME_AFTER_UPLOAD_MS;
  const timer = setTimeout(()=>{
    _keepaliveState.resumeTimers.delete(reason);
    _keepaliveState.pauseReasons.delete(reason);
  }, ms);
  const prev = _keepaliveState.resumeTimers.get(reason);
  if (prev){
    clearTimeout(prev);
  }
  _keepaliveState.resumeTimers.set(reason, timer);
}

function _startKeepAlive(){
  if (_keepaliveState.timer) return;
  _keepaliveState.timer = setInterval(()=>{
    if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
    if (_keepaliveState.pauseReasons.size) return;
    if (!_keepaliveState.providerReady) return;
    const now = Date.now();
    if (now - _lastUserSendTs < _KEEPALIVE_IDLE_THRESHOLD_MS) return;
    if (now - _keepaliveState.lastSentTs < _KEEPALIVE_IDLE_THRESHOLD_MS) return;
    try {
      _ws.send(JSON.stringify({type:"KeepAlive"}));
      _keepaliveState.lastSentTs = now;
    } catch {}
  }, _KEEPALIVE_POLL_MS);
}

function _stopKeepAlive(){
  _clearKeepaliveTimer();
  _resetKeepaliveState(false);
}

function _handleProviderSignal(obj){
  const type = (obj && obj.type ? String(obj.type) : '').toLowerCase();
  const provider = (obj && (obj.provider || obj.service || obj.source || obj.name) ? String(obj.provider || obj.service || obj.source || obj.name) : '').toLowerCase();
  const state = (obj && (obj.state || obj.status || obj.phase)) ? String(obj.state || obj.status || obj.phase).toLowerCase() : '';
  const compactType = type.replace(/[^a-z]/g, '');
  const compactProvider = provider.replace(/[^a-z]/g, '');
  const typeAsProvider = compactType.replace(/(?:provider)?(?:state|status)$/, '');
  const vendor = (obj && obj.vendor ? String(obj.vendor) : '').toLowerCase();
  const compactVendor = vendor.replace(/[^a-z]/g, '');

  const looksDeepgram =
    compactType.includes('deepgram') ||
    compactProvider === 'deepgram' ||
    typeAsProvider === 'deepgram' ||
    compactVendor === 'deepgram';
  if (looksDeepgram && !_keepaliveState.sawFirstDeepgram){
    _keepaliveState.sawFirstDeepgram = true;
    _startKeepAlive();
  }

  const markReady = () => {
    const now = Date.now();
    if (
      _keepaliveState.lastReadyAt &&
      now - _keepaliveState.lastReadyAt < 800
    ){
      return;
    }
    _keepaliveState.lastReadyAt = now;
    if (!_keepaliveState.providerReady){
      _keepaliveState.providerReady = true;
    }
    if (_keepaliveState.providerGateTimer){
      clearTimeout(_keepaliveState.providerGateTimer);
      _keepaliveState.providerGateTimer = null;
    }
    _resumeKeepalive(_KEEPALIVE_REASON_PROVIDER);
  };
  const markNotReady = () => {
    if (_keepaliveState.providerReady){
      _keepaliveState.providerReady = false;
    }
    _pauseKeepalive(_KEEPALIVE_REASON_PROVIDER);
    _scheduleProviderFallback();
  };

  if (!type) return;

  if (compactType === 'asropen' || compactType === 'deepgramopen' || compactType === 'deepgramready'){ markReady(); return; }
  if (compactType === 'asrclose' || compactType === 'asrclosed' || compactType === 'deepgramclosed' || compactType === 'asrerror'){ markNotReady(); return; }

  const providerTargets = ['deepgram', 'asr', 'stt'];
  const applies = compactProvider ? providerTargets.includes(compactProvider) : false;
  if (compactType === 'providerstate' || compactType === 'asrproviderstate' || applies || providerTargets.includes(typeAsProvider)){
    if (applies || providerTargets.includes(typeAsProvider)){
      if (state){
        if (['ready','open','connected','online','live'].includes(state)){
          markReady();
          return;
        }
        if (['closing','closed','error','offline','disconnected','failed'].includes(state)){
          markNotReady();
          return;
        }
        if (['connecting','opening','starting','init','pending'].includes(state)){
          markNotReady();
          return;
        }
      }
    }
  }
}

// --- Auth helper: short-lived WS token ---
async function _getWSToken(sid){
  const r = await fetch(`/api/v1/auth/ws-token?session_id=${encodeURIComponent(sid)}`, { credentials: 'include' });
  if (!r.ok) throw new Error(`ws-token HTTP ${r.status}`);
  const j = await r.json();
  if (!j || !j.token) throw new Error('ws-token missing');
  return j.token;
}

// schedule reconnect if a session is active and we are not already reconnecting
function _scheduleReconnect(){
  if (!__WS_RECONNECT_ENABLED) return;
  // pages set this after greet: window.__askchip_session_started = true
  if (!window.__askchip_session_started) return;
  if (_reconnecting) return;

  _reconnecting = true;
  clearTimeout(_reconnectTimer);

  // light jitter to avoid thundering herd
  const jitter = 0.85 + Math.random() * 0.3; // 0.85x–1.15x
  const delay = Math.min(_backoff * jitter, _BACKOFF_MAX);

  _reconnectTimer = setTimeout(async ()=>{
    _reconnecting = false;
    try {
      await openWS();         // will reuse if already connecting/open
      // exponential backoff on next failure
      _backoff = Math.min(_backoff * 2, _BACKOFF_MAX);
    } catch {
      // if openWS threw synchronously (unlikely), reschedule
      _scheduleReconnect();
    }
  }, delay);

  // shrink backoff if we remain healthy for a while
  setTimeout(()=>{ _backoff = 500; }, _BACKOFF_RESET_MS);
}

// Proactively try to reconnect when network returns
window.addEventListener('online', () => {
  if (window.__askchip_session_started && (!isOpen() && !isConnecting())) {
    _scheduleReconnect();
  }
});

// --- Open WS using subprotocol auth (no token in URL, no headers) ---
// Idempotent: if OPEN/CONNECTING, returns that socket.
export function openWS(options = {}){
  try {
    if (options && Object.prototype.hasOwnProperty.call(options, 'reconnect')) {
      __WS_RECONNECT_ENABLED = !!options.reconnect;
    }
  } catch(_) {}

  try {
    const stamp = {
      module: import.meta && import.meta.url ? import.meta.url : undefined,
      version: (() => {
        try {
          if (!import.meta || !import.meta.url) return undefined;
          const u = new URL(import.meta.url, location.href);
          return u.searchParams.get('v') || undefined;
        } catch (_) {
          return undefined;
        }
      })(),
      flags: (() => {
        try {
          const cfg = window.__askchip_config || {};
          return cfg.flags || cfg.ws_flags || undefined;
        } catch (_) {
          return undefined;
        }
      })(),
      reconnectEnabled: __WS_RECONNECT_ENABLED,
      sessionStarted: !!(window && window.__askchip_session_started),
    };
    _console('info', '[ws] openWS build', stamp);
  } catch (_) {}

  try {
    _console('info', '[ws] openWS options=', options, 'reconnect=', __WS_RECONNECT_ENABLED);
  } catch (_) {}

  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)){
    return Promise.resolve(_ws);
  }

  if (_openPromise){
    return _openPromise;
  }

  _openPromise = (async () => {
    const sid = getSID();

    // If there's a closing/closed socket, let GC take it and create a new one
    _gotReady = false;

    const base = location.origin.replace(/^http/, 'ws');
    const url = new URL(base + '/ws/v1/chat');
    url.searchParams.set('session_id', sid);

    const token = await _getWSToken(sid);
    const subprotocols = ['bearer', `bearer.${token.replace(/=+$/,'')}`]; // padding-less safe

    const ws = new WebSocket(url.toString(), subprotocols);
    ws.binaryType = 'arraybuffer';
    _ws = ws; // assign immediately so helpers see it

    ws.onopen = () => {
      _notifyOpen();
      _clearKeepaliveTimer();
      _resetKeepaliveState(true);
      _reconnecting = false;
      _backoff = 500; // successful open → reset backoff
      try { window.dispatchEvent(new CustomEvent('askchip-ws-open')); } catch {}
    };

    ws.onclose = (e) => {
      const sessionActive = !!(window && window.__askchip_session_started);
      const shouldReconnect = __WS_RECONNECT_ENABLED && sessionActive;

      _stopKeepAlive();

      if (!shouldReconnect) {
        try { window.__askchip_session_started = false; } catch {}
      } else {
        // If we closed before receiving server "ready", a transient connect race happened.
        if (!_gotReady){
          _scheduleReconnect();
        } else {
          // If already "ready", only reconnect if session is active
          _scheduleReconnect();
        }
      }

      try {
        window.dispatchEvent(new CustomEvent('askchip-ws-close', { detail: { code: e.code, reason: e.reason }}));
      } catch {}
      if (!shouldReconnect) {
        return;
      }
    };

    ws.onerror = (e) => _console('warn', '[ws] error', e);

    ws.onmessage = (ev) => {
      try{
        if (typeof ev.data === 'string'){
          const obj = JSON.parse(ev.data);
          const t = obj && obj.type;

          if (t === 'Result' || t === 'Results') {
            const channel = obj && obj.channel;
            const alternatives = channel && channel.alternatives;
            const transcript = alternatives && alternatives[0] && alternatives[0].transcript;
            const isFinal =
              (channel && typeof channel.is_final === 'boolean' && channel.is_final) ||
              (typeof obj.final === 'boolean' && obj.final) ||
              false;
            const chars = typeof transcript === 'string' ? transcript.length : 0;
            const kind = isFinal ? 'final' : 'partial';
            _wsLog('info', `ASR result (${kind})`, {
              final: isFinal,
              chars,
              text: transcript || '',
              frameType: t,
            });
          }
          
          _handleProviderSignal(obj);

          if (t === 'ready') {
            _gotReady = true;
            // small audio unlock nudge on first ready (harmless if already unlocked)
            try { unlockAudio().catch(()=>{}); } catch {}
          }

          // Re-emit as DOM events so UI can respond
          window.dispatchEvent(new CustomEvent('askchip-ws', { detail: obj }));

          // --- Audio routing (WS-only) ---------------------------------------
          if (t === 'assistant_audio') {
            // Server provides { mime, audio_chunks:[], is_last }
            playStream(obj);
            return;
          }
          if (t === 'UtteranceEnd') {
            // Authoritative end-of-utterance: drain and endOfStream
            audioEnd();
            return;
          }

          // --- Other control/info messages -----------------------------------
          if (t === 'KeepAliveAck'){
            // no-op
          } else if (t === 'Error'){
            _console('warn', '[ws] server error:', obj.code, obj.message);
          } else if (t === 'assistant_end'){
            // Text is done. Do NOT teardown audio here (audio ends on UtteranceEnd).
          } else if (t === 'TTSChunk'){
            // (legacy/future) not used; all audio uses assistant_audio frames now.
          }
        } else {
          // Binary from server (future path not used in v1 WS-only)
        }
      }catch(err){
        _console('warn', '[ws] message error', err);
      }
    };

    // be a good citizen on unload — send a normal "going away" close
    window.addEventListener('beforeunload', () => {
      try { ws.close(1001, 'page_unload'); } catch {}
    }, { once:true });

    return ws;
  })().catch((err) => {
    if (_ws && _ws.readyState !== WebSocket.OPEN && _ws.readyState !== WebSocket.CONNECTING){
      try { _ws.close(1000, 'open_failed'); } catch {}
      _ws = null;
    }
    throw err;
  }).finally(() => {
    _openPromise = null;
  });

  return _openPromise;
}

// NOTE: updated to accept a code + reason so we emit a clean close (avoids 1005).
export function closeWS(code = 1000, reason = ''){
  _stopKeepAlive();
  clearTimeout(_reconnectTimer);
  _reconnecting = false;
  try { window.__askchip_session_started = false; } catch {}
  try {
    if (_ws) _ws.close(code, reason || undefined);
  } catch {}
  _ws = null;
}

export function sendJSON(obj){
  if (!_ws || _ws.readyState !== WebSocket.OPEN) return false;
  try {
    const s = JSON.stringify(obj);
    if (bufferedAmount() > 5_000_000) { // ~5MB buffer guardrail
      _console('warn', '[ws] bufferedAmount high; sending anyway', bufferedAmount());
    }
    _ws.send(s);
    _lastUserSendTs = Date.now();
    if (obj && obj.type === 'CloseStream') {
      try { console.info('CloseStream ack'); } catch {}
    }
    return true;
  } catch(e){
    _console('warn', '[ws] sendJSON error', e);
    return false;
  }
}

export async function sendAudioChunk(blob){
  if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
  _pauseKeepalive(_KEEPALIVE_REASON_UPLOAD);
  try{
    const buf = await blob.arrayBuffer();
    try {
      _console('debug', '[ws] sending audio chunk', { bytes: buf.byteLength, mime: blob.type });
    } catch {}
    _ws.send(buf);
    _lastUserSendTs = Date.now();
  }catch(e){
    _console('warn', '[ws] sendAudioChunk error', e);
  } finally {
    _scheduleKeepaliveResume(_KEEPALIVE_REASON_UPLOAD, _KEEPALIVE_RESUME_AFTER_UPLOAD_MS);
  }
}

export async function sendCloseStream(){
  // Drain the WS send buffer before closing the user turn.
  try{
    const deadline = Date.now() + 1500; // up to ~1.5s drain budget
    // small sleep helper
    const sleep = (ms)=> new Promise(r=>setTimeout(r, ms));
    // allow two cycles to ensure MediaRecorder 'stop' enqueued the last blob
    await sleep(20);
    await sleep(20);
    // drain bufferedAmount down near zero, without blocking forever
    while (isOpen() && bufferedAmount() > 0 && Date.now() < deadline){
      await sleep(20);
    }
  }catch(e){
    _console('warn', '[ws] sendCloseStream drain failed', e);
  } finally {
    try { _console('debug', '[DBG] UI→WS', { type: 'CloseStream' }); } catch {}
    sendJSON({ type: "CloseStream" });
  }
}

export function configure(opts = {}){
  // ensure session id is included if caller forgot
  const sid = getSID();
  if (_lastConfigureSid !== sid) {
    _lastConfigureSid = sid;
    _greetSeqCounter = 0;
  }
  const payload = { type: "Configure", session_id: sid, ...opts };
  if (payload.greet) {
    _greetSeqCounter += 1;
    payload.greet_seq = _greetSeqCounter;
    try {
      window.__askchip_last_greet_seq = _greetSeqCounter;
    } catch {}
  }
  sendJSON(payload);
}

export const __TEST_ONLY__ = {
  wsLog: _wsLog,
  console: _console,
};

// Diagnostic: hold UI from ending too quickly unless explicitly allowed.
// Opt-in: localStorage.DIAG_MIN_SESSION_MS = '3500'
(function __installDiagMinSessionHold(){
  try {
    const minMs = parseInt((localStorage && localStorage.DIAG_MIN_SESSION_MS) || '0', 10);
    if (!minMs || isNaN(minMs) || minMs <= 0) return;
    const t0 = Date.now();
    const _oldClose = WebSocket.prototype.close;
    WebSocket.prototype.close = function(...args){
      const elapsed = Date.now() - t0;
      if (elapsed < minMs) {
        try { console.info(`[diag] deferring WS close ${elapsed}ms < ${minMs}`); } catch {}
        setTimeout(() => { try { _oldClose.apply(this, args); } catch {} }, (minMs - elapsed));
        return;
      }
      return _oldClose.apply(this, args);
    };
  } catch {}
})();

