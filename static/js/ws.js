
/* static/js/ws.js — concrete implementation */

import { playStream, stopPlayback, setVisemeCallback } from "./audio.js";
import { renderSuggestions } from "./suggestions.js";

let _ws = null;
let _url = null;
let _onOpenCbs = [];
let _audioChunks = [];
let _textBuf = "";
let _assistantDiv = null;

function getSID(){
  const key = "chip.sid";
  try{
    let sid = sessionStorage.getItem(key);
    if (!sid){
      sid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
      sessionStorage.setItem(key, sid);
    }
    return sid;
  }catch(_){ return String(Date.now()); }
}

export function isOpen(){ return _ws && _ws.readyState === WebSocket.OPEN; }

export function bindControls(startBtn, endBtn){
  if (!startBtn || !endBtn) return;
  function setBusy(busy){
    if (startBtn) startBtn.disabled = busy || !window.AC_AUTH_READY;
    if (endBtn)   endBtn.disabled   = !busy;
  }
  setBusy(false);
  _onOpenCbs.push(()=> setBusy(true));
  addEventListener('beforeunload', ()=> { try{ if (_ws) _ws.close(); }catch{} });
}

export function openWS(){
  if (isOpen()) return _ws;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const q = new URLSearchParams({ session_id: getSID(), tab: crypto.randomUUID() }).toString();
  _url = `${proto}://${location.host}/ws/v1/chat?${q}`;
  _ws = new WebSocket(_url);
  _ws.onopen = () => {
    for (const cb of _onOpenCbs) try{ cb(); }catch{}
  };
  _ws.onclose = () => {};
  _ws.onerror = () => {};
  _ws.onmessage = (ev) => {
    try{
      const fr = JSON.parse(ev.data);
      const t  = fr.type || fr.kind;
      if (t === "assistant_chunk" || t === "text"){
        const text = fr.text || "";
        if (!_assistantDiv){
          const box = document.getElementById('chatMessages');
          if (box){
            _assistantDiv = document.createElement('div');
            _assistantDiv.className = "msg assistant";
            _assistantDiv.textContent = "";
            box.appendChild(_assistantDiv);
          }
        }
        _textBuf += text;
        if (_assistantDiv) _assistantDiv.textContent = _textBuf;
      } else if (t === "audio_chunk"){
        const b64 = fr.data || fr.bytes || "";
        if (b64) _audioChunks.push(Uint8Array.from(atob(b64), c=>c.charCodeAt(0)));
      } else if (t === "visemes"){
        try{ setVisemeCallback(()=>{}); }catch{}
      } else if (t === "suggestions"){
        const list = fr.items || fr.suggestions || [];
        renderSuggestions(list, (s) => {
          const c = document.getElementById('composer');
          if (c) c.value = s;
          const btn = document.getElementById('composerSend');
          if (btn) btn.click();
        });
      } else if (t === "end"){
        // finalize audio
        if (_audioChunks.length){
          const chunks = _audioChunks.slice();
          _audioChunks = [];
          playStream(chunks, []);
        }
        _textBuf = "";
        _assistantDiv = null;
      }
    }catch(_){}
  };
  return _ws;
}

export function waitWSOpen(timeoutMs = 4000){
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    function check(){
      if (isOpen()) return resolve();
      if (Date.now() - t0 > timeoutMs) return reject(new Error("WS open timeout"));
      setTimeout(check, 50);
    }
    check();
  });
}

export function closeWS(){
  try{ if (_ws) _ws.close(); }catch{}
  _ws = null;
  _audioChunks = [];
  _textBuf = "";
  _assistantDiv = null;
}

export function sendInterrupt(){
  try{ if (isOpen()) _ws.send(JSON.stringify({type:'interrupt'})); }catch{}
}

export function cancelNudge(){
  try{ if (isOpen()) _ws.send(JSON.stringify({type:'cancel_nudge'})); }catch{}
}
