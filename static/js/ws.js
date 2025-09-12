import { playStream, setVisemeCallback } from './audio.js';
import { armVAD, disarmVAD, setVadBoost, initMic, currentStream } from './voice.js';
import { ensureCSRF } from './csrf.js';

let _ws=null,_url=null,_onOpen=[],_audio=[],_text='',_div=null,_ping=null;

function sid(){
  const k='chip.sid';
  let s=localStorage.getItem(k);
  if(!s){ s=crypto.randomUUID(); localStorage.setItem(k,s); }
  return s;
}

export function isOpen(){return _ws&&_ws.readyState===WebSocket.OPEN;}
export function waitWSOpen(){return new Promise(res=>{if(isOpen())return res();_onOpen.push(res);});}

async function _tryStartStreamingOnce(){
  // Ensure the mode is fetched (server returns "stream")
  try { await (window.ASKCHIP_FETCH_STT_MODE && window.ASKCHIP_FETCH_STT_MODE()); } catch(e){}
  const s = currentStream ? currentStream() : null;
  if(!s) return; // mic not opened yet
  const sess = window.__ASKCHIP_SESSION_ID || sid();
  let csrf = "";
  try { csrf = await ensureCSRF(); } catch(e){}
  try {
    window.ASKCHIP_START_TIMESLICE_IF_ENABLED && window.ASKCHIP_START_TIMESLICE_IF_ENABLED(s, sess, csrf);
  } catch(e){}
}

export function openWS(){
  if(isOpen())return _ws;
  const proto = location.protocol==='https:'?'wss':'ws';
  const _sid = sid();
  const q=new URLSearchParams({session_id:_sid,tab:crypto.randomUUID()}).toString();
  _url=`${proto}://${location.host}/ws/v1/chat?${q}`;
  _ws=new WebSocket(_url);

  // Expose the WS session id to other modules (voice.js timeslices)
  try { window.__ASKCHIP_SESSION_ID = _sid; } catch(e){}

  _ws.onopen=()=>{
    try{ clearInterval(_ping); }catch{}
    try{ if(isOpen()) _ws.send(JSON.stringify({type:'ping',ts:Date.now()})); }catch{}
    _ping=setInterval(()=>{ try{ if(isOpen()) _ws.send(JSON.stringify({type:'ping',ts:Date.now()})); }catch{} },20000);

    // allow listeners waiting on open
    for(const cb of _onOpen) { try{ cb(); }catch{} }
    _onOpen = [];

    // Prime streaming on connect (will no-op if mic not open yet)
    _tryStartStreamingOnce();
  };

  _ws.onclose=()=>{ try{clearInterval(_ping);}catch{} _ping=null; };
  _ws.onerror=()=>{};

  _ws.onmessage=(ev)=>{
    try{
      const fr=JSON.parse(ev.data);
      const t=fr.type||fr.kind;
      if(t==='ready'){
        // make sure streaming is armed as soon as we're ready
        _tryStartStreamingOnce();
        return;
      }
      if(t==='state'){
        const ph = fr.phase || '';
        if(ph==='assistant_speaking'){
          try{ setVadBoost(1.9); armVAD(); }catch(e){}
        } else if(ph==='assistant_end' || ph==='ready'){
          try{ setVadBoost(1.0); armVAD(); }catch(e){}
          // also attempt to start streaming when we return to ready
          _tryStartStreamingOnce();
        }
        return;
      }
      if(t==='assistant_chunk'||t==='text'){
        const text=fr.text||'';
        if(!_div){
          const box=document.getElementById('chatMessages');
          if(box){ _div=document.createElement('div'); _div.className='msg assistant'; _div.textContent=''; box.appendChild(_div); }
        }
        _text+=text;
        if(_div)_div.textContent=_text;
      }
      else if(t==='audio_chunk'){
        const b64=fr.base64||fr.data||fr.bytes||'';
        if(b64)_audio.push(Uint8Array.from(atob(b64),c=>c.charCodeAt(0)));
      }
      else if(t==='visemes'){
        try{ setVisemeCallback(()=>{});}catch{}
      }
      else if(t==='assistant_end'||t==='end'){
        if(_audio.length){
          const chunks=_audio.slice(); _audio=[];
          playStream(chunks);
        }
        _text=''; _div=null;
        try{ armVAD(); }catch{}
        // try again after a turn ends in case mic opened mid-turn
        _tryStartStreamingOnce();
      }
    }catch(_){}
  };
  return _ws;
}

export function closeWS(){
  try{ if(isOpen())_ws.close(); }catch{}
  _ws=null; _audio=[]; _text=''; _div=null;
}
