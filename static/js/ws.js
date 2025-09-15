import { playStream, setVisemeCallback, unlockAudio } from './audio.js';
import { armVAD, disarmVAD, setVadBoost, initMic, getCurrentStream } from './voice.js';

let _ws=null,_onOpen=[];
let _audioChunks=[];
let _pendingText='', _turnDiv=null;

function sid(){
  const k='chip.sid';
  let s=localStorage.getItem(k);
  if(!s){ s=crypto.randomUUID(); localStorage.setItem(k,s); }
  return s;
}

export function isOpen(){ return _ws && _ws.readyState===WebSocket.OPEN; }
export function waitWSOpen(){ return new Promise(res=>{ if(isOpen()) return res(); _onOpen.push(res); }); }

function _notifyOpen(){ for(const fn of _onOpen.splice(0)) try{ fn(); }catch{} }

async function _tryStartStreamingOnce(){
  try{
    const stream = getCurrentStream() || await initMic();
    const msgId = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    await armVAD(stream, { userMsgId: msgId });
  }catch(e){
    console.warn('[ws] start streaming failed', e);
  }
}

export function openWS(){
  if (isOpen()) return _ws;
  const url = new URL((location.origin.replace(/^http/, 'ws')) + '/ws/v1/chat');
  url.searchParams.set('session_id', sid());
  _ws = new WebSocket(url.toString());
  _ws.onopen = ()=> _notifyOpen();
  _ws.onerror = (e)=> console.warn('[ws] error', e);
  _ws.onclose = ()=>{ try{ disarmVAD(); }catch{} _ws=null; };

  _ws.onmessage = async (ev)=>{
    try{
      const msg = JSON.parse(ev.data);
      if (!msg || !msg.type) return;
      switch(msg.type){
        case 'state':
          // state: 'speaking'|'thinking'|'ready' etc.
          if (msg.state === 'ready'){
            // WS says assistant is ready to listen again
            await _tryStartStreamingOnce();
          }
          break;
        case 'text':
          {
            const pane = document.getElementById('chatMessages');
            if (!_turnDiv){
              _turnDiv = document.createElement('div');
              _turnDiv.className = 'msg assistant';
              pane && pane.appendChild(_turnDiv);
            }
            _pendingText += msg.text || '';
            _turnDiv.textContent = _pendingText;
          }
          break;
        case 'audio_chunk':
          {
            // bytes as base64 → Uint8Array
            const b64 = msg.bytes || msg.data || '';
            if (b64){
              const bin = atob(b64);
              const len = bin.length;
              const arr = new Uint8Array(len);
              for (let i=0;i<len;i++) arr[i] = bin.charCodeAt(i);
              _audioChunks.push(arr);
            }
          }
          break;
        case 'suggestions':
          {
            // optional: render chips
          }
          break;
        case 'end':
          {
            // Turn is done: play audio and then re-arm VAD after playback ends
            try{
              await unlockAudio();
              if (_audioChunks.length) playStream(_audioChunks);
            }catch(e){}
            _audioChunks = [];
            _pendingText = '';
            _turnDiv = null;
            // Resume listening after TTS ends
            const onEnd = async () => {
              window.removeEventListener('chip:tts-ended', onEnd);
              await _tryStartStreamingOnce();
            };
            window.addEventListener('chip:tts-ended', onEnd);
          }
          break;
      }
    }catch(e){
      console.warn('[ws] message error', e);
    }
  };
  return _ws;
}

export function closeWS(){
  try{ if(isOpen()) _ws.close(); }catch{}
  _ws=null; _audioChunks=[]; _pendingText=''; _turnDiv=null;
}
