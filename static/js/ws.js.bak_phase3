import { playStream, unlockAudio } from './audio.js';
import { armVAD, disarmVAD, initMic, getCurrentStream } from './voice.js';
import { getSID } from './util/sid.js';

let _ws = null;
let _onOpen = [];
let _audioChunks = [];
let _pendingText = '';
let _turnDiv = null;

export function isOpen(){ return _ws && _ws.readyState === WebSocket.OPEN; }
export function waitWSOpen(){ return new Promise(res => { if (isOpen()) return res(); _onOpen.push(res); }); }
function _notifyOpen(){ for (const fn of _onOpen.splice(0)) try{ fn(); }catch{} }

async function _tryStartStreamingOnce(){
  try{
    const stream = getCurrentStream() || await initMic();
    // fresh user_msg_id for this listen cycle
    const msgId = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
    await armVAD(stream, { userMsgId: msgId });
  }catch(e){
    console.warn('[ws] start streaming failed', e);
  }
}

export function openWS(){
  if (isOpen()) return _ws;
  const base = location.origin.replace(/^http/, 'ws');
  const url = new URL(base + '/ws/v1/chat');
  url.searchParams.set('session_id', getSID());

  _ws = new WebSocket(url.toString());
  _ws.onopen = () => _notifyOpen();
  _ws.onerror = (e) => console.warn('[ws] error', e);
  _ws.onclose = () => { try{ disarmVAD(); }catch{} _ws=null; };

  _ws.onmessage = async (ev) => {
    try{
      const msg = JSON.parse(ev.data);
      if (!msg || !msg.type) return;

      switch (msg.type) {
        case 'state':
          // When assistant is ready to listen again
          if (msg.state === 'ready') await _tryStartStreamingOnce();
          break;

        case 'text': {
          const pane = document.getElementById('chatMessages');
          if (!_turnDiv) {
            _turnDiv = document.createElement('div');
            _turnDiv.className = 'msg assistant';
            pane && pane.appendChild(_turnDiv);
          }
          _pendingText += (msg.text || '');
          _turnDiv.textContent = _pendingText;
          break;
        }

        case 'audio_chunk': {
          const b64 = msg.bytes || msg.data || '';
          if (b64) {
            const bin = atob(b64);
            const len = bin.length;
            const arr = new Uint8Array(len);
            for (let i=0;i<len;i++) arr[i] = bin.charCodeAt(i);
            _audioChunks.push(arr);
          }
          break;
        }

        case 'suggestions':
          // (optional) render suggestion chips here
          break;

        case 'end': {
          // turn finished — play audio, then re-arm after playback
          try{
            await unlockAudio();
            if (_audioChunks.length) playStream(_audioChunks);
          }catch(_){}
          _audioChunks = [];
          _pendingText = '';
          _turnDiv = null;

          const onEnd = async () => {
            window.removeEventListener('chip:tts-ended', onEnd);
            await _tryStartStreamingOnce();
          };
          window.addEventListener('chip:tts-ended', onEnd);
          break;
        }
      }
    }catch(e){
      console.warn('[ws] message error', e);
    }
  };

  return _ws;
}

export function closeWS(){
  try{ if (isOpen()) _ws.close(); }catch{}
  _ws = null; _audioChunks = []; _pendingText = ''; _turnDiv = null;
}
