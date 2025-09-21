// static/js/ws.js — Phase 4/5 hardened: single socket + reconnection + helpers
// Responsibilities:
//   • Own a single WebSocket connection per tab to /ws/v1/chat
//   • Subprotocol auth (bearer + bearer.<token>)
//   • Auto-reconnect with bounded backoff (only if session is active)
//   • Helpers: waitWSOpen(), isOpen(), bufferedAmount(), sendJSON/audio
//   • DOM events: 'askchip-ws' (messages), 'askchip-ws-close' (terminal close)

import { playStream, audioEnd, unlockAudio } from './audio.js';
import { getSID } from './util/sid.js';

let _ws = null;
let _onOpen = [];
let _keepaliveTimer = null;
let _lastUserSendTs = 0;
let _gotReady = false;

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

function _startKeepAlive(){
  _stopKeepAlive();
  _keepaliveTimer = setInterval(()=>{
    if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    if (now - _lastUserSendTs > 3500){
      try{ _ws.send(JSON.stringify({type:"KeepAlive"})); }catch{}
    }
  }, 4000);
}
function _stopKeepAlive(){ if (_keepaliveTimer){ clearInterval(_keepaliveTimer); _keepaliveTimer = null; }}

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
  // pages set this after greet: window.__askchip_session_started = true
  if (!window.__askchip_session_started) return;
  if (_reconnecting) return;

  _reconnecting = true;
  clearTimeout(_reconnectTimer);

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
  }, _backoff);

  // reduce backoff again if we stay connected for a while
  setTimeout(()=>{ _backoff = Math.min(_backoff, 1000); }, _BACKOFF_RESET_MS);
}

// --- Open WS using subprotocol auth (no token in URL, no headers) ---
// Idempotent: if OPEN/CONNECTING, returns that socket.
export async function openWS(){
  const sid = getSID();

  // Reuse socket if already open/connecting
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)){
    return _ws;
  }

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
    _startKeepAlive();
    _reconnecting = false;
    _backoff = 500; // successful open → reset backoff
  };

  ws.onclose = (e) => {
    _stopKeepAlive();

    // If we closed before receiving server "ready", a transient connect race happened.
    if (!_gotReady){
      _scheduleReconnect();
    } else {
      // If already "ready", only reconnect if session is active
      _scheduleReconnect();
    }

    try {
      window.dispatchEvent(new CustomEvent('askchip-ws-close', { detail: { code: e.code, reason: e.reason }}));
    } catch {}
  };

  ws.onerror = (e) => console.warn('[ws] error', e);

  ws.onmessage = (ev) => {
    try{
      if (typeof ev.data === 'string'){
        const obj = JSON.parse(ev.data);
        const t = obj && obj.type;

        if (t === 'ready') _gotReady = true;

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
          console.warn('[ws] server error:', obj.code, obj.message);
        } else if (t === 'assistant_end'){
          // Text is done. Do NOT teardown audio here (audio ends on UtteranceEnd).
        } else if (t === 'TTSChunk'){
          // (legacy/future) not used; all audio uses assistant_audio frames now.
        }
      } else {
        // Binary from server (future path not used in v1 WS-only)
      }
    }catch(err){
      console.warn('[ws] message error', err);
    }
  };

  // be a good citizen on unload — send a normal "going away" close
  window.addEventListener('beforeunload', () => {
    try { ws.close(1001, 'page_unload'); } catch {}
  }, { once:true });

  return ws;
}

// NOTE: updated to accept a code + reason so we emit a clean close (avoids 1005).
export function closeWS(code = 1000, reason = ''){
  _stopKeepAlive();
  clearTimeout(_reconnectTimer);
  _reconnecting = false;
  try {
    if (_ws) _ws.close(code, reason || undefined);
  } catch {}
  _ws = null;
}

export function sendJSON(obj){
  if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
  try {
    _ws.send(JSON.stringify(obj));
    _lastUserSendTs = Date.now();
  } catch(e){
    console.warn('[ws] sendJSON error', e);
  }
}

export async function sendAudioChunk(blob){
  if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
  try{
    const buf = await blob.arrayBuffer();
    _ws.send(buf);
    _lastUserSendTs = Date.now();
  }catch(e){
    console.warn('[ws] sendAudioChunk error', e);
  }
}

export function sendCloseStream(){
  sendJSON({ type: "CloseStream" });
}

export function configure(opts = {}){
  sendJSON({ type: "Configure", ...opts });
}
