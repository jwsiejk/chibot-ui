
// main.js — New API + awareness signals + suggestions + silence nudge
console.log("[AC][BOOT] main.js (new API + awareness) loaded");

import { $, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import { _chipGuide, _chipSetState, _chipStep } from "./core/state.js";
import { appendMessage, _chipRenderSuggestions } from "./chat/ui.js";

import { start as chatStart, attachSocket, handleVoiceOnceResponse, sendUserText, requestNudge } from "./chat/send.js";
import * as VAD from "./voice/vad.js";
import * as REC from "./voice/recorder-lite.js";
import { loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

let lastAssistantEndAt = 0, lastInterruptReason = null, nudgeWasSent = false, lastLatencyMs = null;
let bargeInTrail = []; // last 3 reasons
let turnStartAt = 0;

const _dbg = { lane:"live", mic:{ permission:"unknown", armed:false, rec:false }, counters:{ vadStart:0, vadStop:0, sttPosts:0, wsSends:0 } };
const el = (id)=>document.getElementById(id);
let BTN_START, chatInputEl, _nudgeTimer=null, _nudgeArmed=false;
const NUDGE_MS = window.CHIP_NUDGE_MS || 4200;

function hud(){
  let d=document.getElementById("ac-debug-hud");
  if(!d){
    d=document.createElement("div"); d.id="ac-debug-hud";
    d.style.position="fixed"; d.style.right="14px"; d.style.bottom="14px"; d.style.zIndex=9999;
    d.style.font="12px/1.4 system-ui, -apple-system, Segoe UI, Roboto";
    d.style.background="rgba(0,0,0,0.7)"; d.style.color="#cbe4ff"; d.style.border="1px solid rgba(255,255,255,0.2)";
    d.style.padding="8px 10px"; d.style.borderRadius="6px"; d.style.maxWidth="42vw"; d.style.pointerEvents="none";
    document.body.appendChild(d);
  }
  return d;
}
function updateHUD(extra){
  const d = hud();
  d.innerHTML = `<b>Ask Chip · Debug</b><br/>mic=${_dbg.mic.armed?'armed':'off'}, rec=${_dbg.mic.rec?'on':'off'} | perm=${_dbg.mic.permission}<br/>vadStart=${_dbg.counters.vadStart} vadStop=${_dbg.counters.vadStop} sttPosts=${_dbg.counters.sttPosts} wsSends=${_dbg.counters.wsSends}${extra?`<br/>${extra}`:''}`;
}

// Suggestions UI (from server)
window.addEventListener('chip:suggestions', (e)=>{
  const items = e.detail || [];
  _chipRenderSuggestions(items.map(x => (x.label || x).toString().slice(0, 60)), (label)=>{
    sendUserText(label, {});
  });
});

// Append assistant text to the log (if server sends text frames)
window.addEventListener('chip:text', (e)=>{
  const msg = e.detail;
  if (msg && typeof msg.text === 'string') appendMessage('assistant', msg.text);
});

// Nudge lifecycle
function armNudge(){ if (_nudgeArmed) return; _nudgeArmed=true; _nudgeTimer=setTimeout(()=>{ _nudgeTimer=null; _nudgeArmed=false; nudgeWasSent = true; requestNudge('silence_timeout'); setTimeout(()=>{ nudgeWasSent=false; }, 6000); }, NUDGE_MS); }
function cancelNudge(){ if (_nudgeTimer){ clearTimeout(_nudgeTimer); _nudgeTimer=null; } _nudgeArmed=false; }

// Assistant lifecycle hooks to manage nudges and latency reference
window.addEventListener('chip:assistant_speaking', ()=>{ cancelNudge(); });
window.addEventListener('chip:assistant_end', ()=>{ lastAssistantEndAt = Date.now(); armNudge(); });
window.addEventListener('chip:interrupt', (e)=>{ lastInterruptReason = (e && e.detail && e.detail.reason) || 'unknown'; bargeInTrail.push(lastInterruptReason); if (bargeInTrail.length>3) bargeInTrail.shift(); cancelNudge(); });

// UI wiring
function initUI(){
  BTN_START = el("zStart");
  chatInputEl = el("chatInput");
  if (chatInputEl){
    chatInputEl.addEventListener("keydown", (e)=>{
      if (_nudgeTimer) { clearTimeout(_nudgeTimer); _nudgeTimer=null; _nudgeArmed=false; }
      if (e.key === "Enter" && !e.shiftKey){
        e.preventDefault();
        const text = chatInputEl.value.trim();
        if (text){
          sendUserText(text, {}); _dbg.counters.wsSends++; updateHUD();
          appendMessage("user", text, "text");
          chatInputEl.value="";
        }
      }
    });
  }
}

// Start / session
async function startSession(){
  await chatStart();
  // Recorder
  REC.init({
    getStream: VAD.getStream,
    async onBlob(blob){
      _dbg.counters.sttPosts++; updateHUD();
      // Assemble awareness meta
      try {
        const metrics = VAD.getLastSegmentMetrics && VAD.getLastSegmentMetrics();
        const meta = {
          response_latency_ms: lastAssistantEndAt ? (turnStartAt - lastAssistantEndAt) : null,
          interruption_during_tts: !!(lastInterruptReason && lastInterruptReason !== 'keyboard'),
          speech_ms: metrics?.speech_ms ?? (turnStartAt ? (Date.now()-turnStartAt) : null),
          avg_rms: metrics?.avg_rms ?? null,
          max_rms: metrics?.max_rms ?? null,
          wakeword_used: lastInterruptReason === 'wakeword',
          silence_nudge_fired: !!nudgeWasSent,
          num_bargeins_last_3_turns: bargeInTrail.filter(x => x === 'vad' || x === 'wakeword').length
        };
        const form = new FormData();
        form.append("file", blob, "clip.webm");
        form.append("mime", "audio/webm");
        form.append("meta", new Blob([JSON.stringify(meta)], { type:"application/json" }));
        await fetch("/api/v1/stt", { method:"POST", body: form });
      } catch(e){
        console.warn("STT post failed", e);
      }
      // Reset lastInterruptReason after a completed user turn
      lastInterruptReason = null;
    }
  });
  // VAD triggers recording + latency baseline
  VAD.on("speechstart", ()=>{ _dbg.counters.vadStart++; _dbg.mic.rec=true; updateHUD(); cancelNudge(); turnStartAt = Date.now(); });
  VAD.on("speechend",  ()=>{ _dbg.counters.vadStop++;  _dbg.mic.rec=false; updateHUD(); REC.stop(); });

  // WS
  const ws = new WebSocket(location.origin.replace(/^http/,"ws") + "/ws/v1/chat");
  attachSocket(ws);
  ws.addEventListener("message", handleVoiceOnceResponse);

  _dbg.mic.armed=true; updateHUD();
}

async function boot(){
  setToolbarHeightVar();
  wireLoginAndProfileHandlers();
  await loadProfileIntoForm();
  await gate();
  initUI();
  BTN_START?.addEventListener("click", startSession);
  if (window.CHIP_AUTOSTART) startSession();
  updateHUD();
}

window.addEventListener("DOMContentLoaded", boot);
