
// static/js/ws.js — Phase 4: single socket for audio + control (WS-only)
// Responsibilities:
//   • Own a single WebSocket connection per tab to /ws/v1/chat
//   • Provide helpers to send JSON control frames and binary audio
//   • Dispatch browser events for server messages (Results, UtteranceEnd, KeepAliveAck, Error)
//   • Expose backpressure info for MediaRecorder to pause/resume intelligently

import { ChunkedAudioPlayer } from './audio_player.js';
import { unlockAudio } from './audio.js';
import { getSID } from './util/sid.js';

let _ws = null;
let _onOpen = [];
let _keepaliveTimer = null;
let _lastUserSendTs = 0;
let _player = null;

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

export function openWS(){
  if (isOpen()) return _ws;
  const base = location.origin.replace(/^http/, 'ws');
  const url = new URL(base + '/ws/v1/chat');
  url.searchParams.set('session_id', getSID());
  // Single-socket policy: close any previous socket
  if (_ws && _ws.readyState === WebSocket.OPEN){
    try{ _ws.close(); }catch{}
  }
  _ws = new WebSocket(url.toString());

  _ws.binaryType = 'arraybuffer';
  _ws.onopen = () => { _notifyOpen(); _startKeepAlive(); };
  _ws.onclose = () => { _stopKeepAlive(); };
  _ws.onerror = (e) => console.warn('[ws] error', e);
  _ws.onmessage = (ev) => {
    try{
      if (typeof ev.data === 'string'){
        const obj = JSON.parse(ev.data);
        const t = obj && obj.type;
        // Re-emit as DOM events so UI can respond
        window.dispatchEvent(new CustomEvent('askchip-ws', { detail: obj }));

        if (t === 'Results'){
          // Optional: could update a transcript area; leave to app.js listener
        } else if (t === 'UtteranceEnd'){
          // Transition UI to thinking; leave to app.js
        } else if (t === 'KeepAliveAck'){
          // no-op
        } else if (t === 'Error'){
          console.warn('[ws] server error:', obj.code, obj.message);
        } else if (t === 'TTSChunk'){
          // Optional future: TTS over WS; not enabled in Phase 4
        }
      } else {
        // Binary from server (e.g., TTS); not used in Phase 4
      }
    }catch(e){
      console.warn('[ws] message error', e);
    }
  };
  return _ws;
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
