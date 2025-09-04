
/**
 * send.js — New API (soft / echo‑aware barge‑in) + assistant lifecycle events + suggestions + requestNudge()
 */
import { SoftBargeIn } from "./soft-bargein.js";
import * as VAD from "../voice/vad.js";

function emitState(state){ try{ window.dispatchEvent(new CustomEvent("chip:state",{detail:{state}})); }catch{} }
function base64ToArrayBuffer(b64){ const s=atob(b64); const a=new Uint8Array(s.length); for(let i=0;i<s.length;i++) a[i]=s.charCodeAt(i); return a.buffer; }

// Fallback HTML5/WebAudio player
function createFallbackTTSPlayer(mime="audio/webm"){
  let chunks=[]; const audio=new Audio(); audio.preload="auto"; let muted=false;
  return{
    appendChunk(data){ const buf=data instanceof ArrayBuffer ? data : (data?.buffer||new ArrayBuffer(0)); chunks.push(new Blob([buf],{type:mime})); },
    finalize(){ if(!chunks.length) return; try{ URL.revokeObjectURL(audio.src);}catch{} const blob=new Blob(chunks,{type:mime}); chunks=[]; const url=URL.createObjectURL(blob); audio.src=url; audio.muted=muted; audio.play().catch(()=>{}); },
    stop(){ try{ audio.pause(); audio.currentTime=0; }catch{} },
    mute(m){ muted=!!m; try{ audio.muted=muted; }catch{} }
  };
}

let ttsPlayer=null, barge=null, wsRef=null, started=false, _assistantSpeaking=false;

export async function start(){
  if (started) return; started=true;
  ttsPlayer = window.ttsPlayer || createFallbackTTSPlayer();
  try{ await VAD.arm(); }catch(e){ console.warn("VAD arm failed:", e); }
  barge = new SoftBargeIn({
    vad: VAD, socket: null, ttsPlayer,
    confirmMs: (window.CHIP_BARGE_CONFIRM_MS || 420),
    echoThresholdBoost: (window.CHIP_ECHO_THRESHOLD_BOOST || 1.9),
    onPendingUI: (isPending)=>{ document.body.classList.toggle("chip-paused-pending", !!isPending); },
    interruptCmd: "interrupt"
  });
  barge.wire();
  window.addEventListener("keydown",(e)=>{ if(e.key==="Escape") interrupt("keyboard"); });
  emitState("ready");
}

export function attachSocket(ws){ wsRef = ws || wsRef; if (barge) barge.socket = wsRef; }
export function setTTSPlayer(player){ ttsPlayer = player || ttsPlayer; }
export function sendUserText(text, ctx={}){ if(!wsRef || wsRef.readyState!==1) return; try{ wsRef.send(JSON.stringify({type:"user",mode:"text",text,ctx})); }catch{} }
export function requestNudge(reason='silence_timeout', meta={}){ if(!wsRef || wsRef.readyState!==1) return; try{ wsRef.send(JSON.stringify({type:'control',cmd:'nudge',reason,meta})); }catch{} }
export function interrupt(reason="manual"){ try{ barge?.immediateInterrupt?.(reason); }catch{} }

export async function handleVoiceOnceResponse(evtOrMsg){
  let msg=null, maybeWS=null;
  if (evtOrMsg && 'data' in evtOrMsg){
    maybeWS = evtOrMsg.currentTarget || evtOrMsg.target || null;
    if (typeof evtOrMsg.data === "string"){ try{ msg=JSON.parse(evtOrMsg.data); }catch{ msg=null; } }
    else {
      let buf; if (evtOrMsg.data instanceof ArrayBuffer) buf = evtOrMsg.data;
      else if (evtOrMsg.data instanceof Blob) buf = await evtOrMsg.data.arrayBuffer();
      if (buf){ 
        if(!_assistantSpeaking){ _assistantSpeaking=true; try{ window.dispatchEvent(new CustomEvent('chip:assistant_speaking')); }catch{} }
        barge?.onAssistantAudioStart(); (ttsPlayer || (ttsPlayer=createFallbackTTSPlayer())).appendChunk(buf); return;
      }
    }
  } else if (evtOrMsg && typeof evtOrMsg === "object"){ msg = evtOrMsg; }

  if (maybeWS && !wsRef) attachSocket(maybeWS);
  if (!msg) return;

  switch(msg.type){
    case "state": emitState(msg.state); break;
    case "text":  try{ window.dispatchEvent(new CustomEvent("chip:text",{detail:msg})); }catch{} break;
    case "suggestions":
      try{ window.dispatchEvent(new CustomEvent("chip:suggestions",{ detail: (msg.items||[]) })); }catch{}
      break;
    case "audio_chunk":{
      if(!_assistantSpeaking){ _assistantSpeaking=true; try{ window.dispatchEvent(new CustomEvent('chip:assistant_speaking')); }catch{} }
      barge?.onAssistantAudioStart();
      let buf; if (typeof msg.data==="string") buf=base64ToArrayBuffer(msg.data);
      else if (msg.data?.type==="Buffer" && Array.isArray(msg.data.data)) buf=new Uint8Array(msg.data.data).buffer;
      else if (Array.isArray(msg.data)) buf=new Uint8Array(msg.data).buffer;
      if (buf) (ttsPlayer || (ttsPlayer=createFallbackTTSPlayer())).appendChunk(buf);
      break;
    }
    case "end":
      barge?.onAssistantAudioEnd();
      (ttsPlayer || (ttsPlayer=createFallbackTTSPlayer())).finalize();
      if(_assistantSpeaking){ _assistantSpeaking=false; try{ window.dispatchEvent(new CustomEvent('chip:assistant_end')); }catch{} }
      emitState("ready");
      break;
    case "error": console.error("WS error msg:", msg); break;
  }
}

// Convenience global
window.ChatSend = { start, attachSocket, handleVoiceOnceResponse, sendUserText, interrupt, setTTSPlayer, requestNudge };
