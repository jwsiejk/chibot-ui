/* static/js/ws.js — explicit connect only (no autoconnect on load) */

let _ws = null;
let _url = null;
let _onOpenCbs = [];

export function bindControls(startBtn, endBtn){
  if (!startBtn || !endBtn) return;
  // Toggle Start/End buttons on socket state
  function setBusy(busy){
    if (startBtn) startBtn.disabled = busy || !window.AC_AUTH_READY;
    if (endBtn)   endBtn.disabled   = !busy;
  }
  setBusy(false);
  _onOpenCbs.push(()=> setBusy(true));
  addEventListener('beforeunload', ()=> { try{ if (_ws) _ws.close(); }catch{} });
  // End button closes
  endBtn.addEventListener('click', ()=> { try{ if (_ws) _ws.close(); }catch{} setBusy(false); });
}

export function openWS(url){
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) return _ws;
  if (!window.AC_AUTH_READY) { console.warn('[ws] blocked until auth ready'); return null; }
  _url = url || _url || makeWSUrl();
  _ws = new WebSocket(_url);
  _ws.addEventListener('open', ()=> { console.info('[ws] open'); _onOpenCbs.forEach(fn=>fn()); });
  _ws.addEventListener('close', ()=> { console.info('[ws] close'); _ws = null; });
  _ws.addEventListener('error', e=> console.warn('[ws] error', e));
  return _ws;
}

export function closeWS(){ try{ if (_ws) _ws.close(); }catch{} }

export function waitWSOpen(){
  return new Promise((resolve, reject)=>{
    if (_ws && _ws.readyState === WebSocket.OPEN) return resolve();
    const t = setTimeout(()=> reject(new Error('ws timeout')), 8000);
    const onOpen = ()=> { clearTimeout(t); resolve(); };
    _onOpenCbs.push(onOpen);
  });
}

export function sendInterrupt(){
  try{ if (_ws && _ws.readyState === WebSocket.OPEN) _ws.send(JSON.stringify({type:'interrupt'})); }catch{}
}

export function cancelNudge(){
  try{ if (_ws && _ws.readyState === WebSocket.OPEN) _ws.send(JSON.stringify({type:'cancel_nudge'})); }catch{}
}

function makeWSUrl(){
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const q = new URLSearchParams({ session_id: crypto.randomUUID(), tab: crypto.randomUUID() }).toString();
  return `${proto}://${location.host}/ws/v1/chat?${q}`;
}
