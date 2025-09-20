// static/js/ws.js — Phase 4: single socket for audio + control (WS-only)
// Responsibilities:
//   • Own a single WebSocket connection per tab to /ws/v1/chat
//   • Provide helpers to send JSON control frames and binary audio
//   • Dispatch browser events for server messages (Results, UtteranceEnd, KeepAliveAck, Error)
//   • Expose backpressure info for MediaRecorder to pause/resume intelligently
//   • Subprotocol auth (bearer + bearer.<token>), with a single retry if no "ready" is received

import { ChunkedAudioPlayer } from './audio_player.js';
import { unlockAudio } from './audio.js';
import { getSID } from './util/sid.js';

let _ws = null;
let _onOpen = [];
let _keepaliveTimer = null;
let _lastUserSendTs = 0;
let _player = null;
let _gotReady = false;

export function isOpen(){ return _ws && _ws.readyState === WebSocket.OPEN; }
export function waitWSOpen(){ return new Promise(res => { if (isOpen()) return res(); _onOpen.push(res); }); }
function _notifyOpen(){ for (const fn of _onOpen.splice(0)) try{ fn(); }catch{} }

export function bufferedAmount(){ return _ws ? _ws.bufferedAmount : 0; }

function _startKeepAlive(){
  _stopKeepAlive();
  _keepaliveTimer = setInterval(()=>{
    if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
    // Send KeepAlive only if we haven't sent anything recently
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
  return j.token;
}

// --- Open WS using subprotocol auth (no token in URL, no headers) ---
// Adds a single retry with jitter if the socket closes before the first "ready".
export async function openWS(){
  const sid = getSID();

  // Single-socket policy: close any previous socket
  if (_ws && _ws.readyState === WebSocket.OPEN){
    try{ _ws.close(); }catch{}
  }

  let attempts = 0;
  const maxRetries = 1;

  async function connect(){
    const base = location.origin.replace(/^http/, 'ws');
    const url = new URL(base + '/ws/v1/chat');
    url.searchParams.set('session_id', sid);

    const token = await _getWSToken(sid);
    const subprotocols = ['bearer', `bearer.${token.replace(/=+$/,'')}`]; // padding-less safe

    _ws = new WebSocket(url.toString(), subprotocols);
    _ws.binaryType = 'arraybuffer';
    _gotReady = false;

    _ws.onopen = () => { _notifyOpen(); _startKeepAlive(); };

    _ws.onclose = async (e) => {
      _stopKeepAlive();
      // If we closed before receiving the server's "ready", retry once with jitter
      if (!_gotReady && attempts < maxRetries){
        attempts++;
        await new Promise(r => setTimeout(r, 200 + Math.random()*300));
        return connect();
      }
      // otherwise, bubble the close (UI may listen on window event)
      try{
        window.dispatchEvent(new CustomEvent('askchip-ws-close', { detail: { code: e.code, reason: e.reason }}));
      }catch{}
    };

    _ws.onerror = (e) => console.warn('[ws] error', e);

    _ws.onmessage = (ev) => {
      try{
        if (typeof ev.data === 'string'){
          const obj = JSON.parse(ev.data);
          const t = obj && obj.type;

          if (t === 'ready') _gotReady = true;

          // Re-emit as DOM events so UI can respond
          window.dispatchEvent(new CustomEvent('askchip-ws', { detail: obj }));

          if (t === 'Results'){
            // Optional: UI updates handled by app.js
          } else if (t === 'UtteranceEnd'){
            // Optional: UI state transition handled by app.js
          } else if (t === 'KeepAliveAck'){
            // no-op
          } else if (t === 'Error'){
            console.warn('[ws] server error:', obj.code, obj.message);
          } else if (t === 'TTSChunk'){
            // not used in Phase 4
          }
        } else {
          // Binary from server (e.g., future TTS); not used in Phase 4
        }
      }catch(err){
        console.warn('[ws] message error', err);
      }
    };

    return _ws;
  }

  return connect();
}

export function closeWS(){
  _stopKeepAlive();
  try{ if (isOpen()) _ws.close(); }catch{}
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
