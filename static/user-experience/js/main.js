
// main.js — New API + awareness (HOTFIX v2: robust greet fallback with response.ok checks)
console.log("[AC][BOOT] main.js (new API + awareness) loaded");

import { $, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import { _chipGuide, _chipSetState, _chipStep } from "./core/state.js";
import { appendMessage } from "./chat/ui.js";

import { start as chatStart, attachSocket, handleVoiceOnceResponse, sendUserText, requestNudge } from "./chat/send.js";
import * as VAD from "./voice/vad.js";
import * as REC from "./voice/recorder-lite.js";
import { loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

const _dbg = { lane:"live", mic:{ permission:"unknown", armed:false, rec:false }, counters:{ vadStart:0, vadStop:0, sttPosts:0, wsSends:0 } };
const el = (id)=>document.getElementById(id);
let BTN_START, chatInputEl, _nudgeTimer=null, _nudgeArmed=false, lastAssistantEnd=0, lastInterruptReason=null, nudgeWasSent=false;
const NUDGE_MS = window.CHIP_NUDGE_MS || 4200;

function hud(){ let d=document.getElementById("ac-debug-hud"); if(!d){ d=document.createElement("div"); d.id="ac-debug-hud"; d.style.position="fixed"; d.style.right="14px"; d.style.bottom="14px"; d.style.zIndex=9999; d.style.font="12px/1.4 system-ui, -apple-system, Segoe UI, Roboto"; d.style.background="rgba(0,0,0,0.7)"; d.style.color="#cbe4ff"; d.style.border="1px solid rgba(255,255,255,0.2)"; d.style.padding="8px 10px"; d.style.borderRadius="6px"; d.style.maxWidth="42vw"; d.style.pointerEvents="none"; document.body.appendChild(d);} return d; }
function updateHUD(extra){ const d=hud(); d.innerHTML = `<b>Ask Chip · Debug</b><br/>mic=${_dbg.mic.armed?'armed':'off'}, rec=${_dbg.mic.rec?'on':'off'} | vadStart=${_dbg.counters.vadStart} vadStop=${_dbg.counters.vadStop} sttPosts=${_dbg.counters.sttPosts} wsSends=${_dbg.counters.wsSends}${extra?`<br/>${extra}`:''}`; }

function initUI(){
  BTN_START = el("zStart");
  chatInputEl = el("chatInput");
  if (chatInputEl){
    chatInputEl.addEventListener("keydown", (e)=>{
      cancelNudge();
      if (e.key==="Enter" && !e.shiftKey){
        e.preventDefault();
        const text = chatInputEl.value.trim();
        if (text){
          sendUserText(text, {}); _dbg.counters.wsSends++; updateHUD();
          appendMessage({ role:"user", content:text });
          chatInputEl.value="";
        }
      }
    });
  }
}

// Nudge control
function armNudge(){ if (_nudgeArmed) return; _nudgeArmed=true; nudgeWasSent=false; _nudgeTimer=setTimeout(()=>{ _nudgeTimer=null; _nudgeArmed=false; nudgeWasSent=true; requestNudge('silence_timeout'); }, NUDGE_MS); }
function cancelNudge(){ if (_nudgeTimer){ clearTimeout(_nudgeTimer); _nudgeTimer=null; } _nudgeArmed=false; }

async function startSession(){
  await chatStart();

  REC.init({
    getStream: VAD.getStream,
    async onBlob(blob){
      _dbg.counters.sttPosts++; updateHUD();
      const now = Date.now();
      const meta = {
        response_latency_ms: lastAssistantEnd ? (now - lastAssistantEnd) : null,
        interruption_during_tts: lastInterruptReason === 'vad' || lastInterruptReason === 'wakeword',
        speech_ms: window.__AC_lastSpeechMs || null,
        avg_rms: window.__AC_lastAvgRms || null,
        max_rms: window.__AC_lastMaxRms || null,
        wakeword_used: lastInterruptReason === 'wakeword',
        silence_nudge_fired: nudgeWasSent || false
      };
      const form = new FormData();
      form.append("file", blob, "clip.webm");
      form.append("mime", "audio/webm");
      form.append("meta", new Blob([JSON.stringify(meta)], { type: "application/json" }));
      await fetch("/api/v1/stt", { method:"POST", body: form });
    }
  });

  // Track VAD metrics for awareness
  VAD.on("speechstart", ()=>{ _dbg.counters.vadStart++; _dbg.mic.rec=true; updateHUD(); cancelNudge(); window.__AC__speechStartAt = performance.now(); });
  VAD.on("speechend",  ()=>{ _dbg.counters.vadStop++;  _dbg.mic.rec=false; updateHUD();
    const end = performance.now(); const start = window.__AC__speechStartAt || end; const ms = Math.max(0, end - start);
    window.__AC_lastSpeechMs = ms;
  });

  // Listen for assistant lifecycle to manage nudges + timestamps
  window.addEventListener("chip:assistant_speaking", ()=>{ cancelNudge(); });
  window.addEventListener("chip:assistant_end", ()=>{ lastAssistantEnd = Date.now(); armNudge(); });

  // Open chat WS
  const ws = new WebSocket(location.origin.replace(/^http/,"ws") + "/ws/v1/chat");
  attachSocket(ws);
  ws.addEventListener("message", handleVoiceOnceResponse);
  ws.addEventListener("open", async ()=>{
    console.log("[AC] WS open → greeting");
    let greeted = false;

    // Try HTTP POST /api/v1/greet (and check status)
    try {
      const r = await fetch("/api/v1/greet", { method:"POST" });
      if (r && r.ok) greeted = true;
    } catch {}

    // Fallback: GET /api/v1/greet (and check status)
    if (!greeted) {
      try {
        const r2 = await fetch("/api/v1/greet");
        if (r2 && r2.ok) greeted = true;
      } catch {}
    }

    // Fallback: WS control {cmd:'greet'}
    if (!greeted) {
      try { ws.send(JSON.stringify({ type:"control", cmd:"greet" })); greeted = true; } catch {}
    }

    // Final fallback: send a neutral user text that servers map to greet
    if (!greeted) {
      try { ws.send(JSON.stringify({ type:"user", mode:"text", text:"/greet" })); greeted = true; } catch {}
    }
  });
  ws.addEventListener("close", ()=>{ console.warn("[AC] WS closed"); });
  ws.addEventListener("error", (e)=>{ console.warn("[AC] WS error", e); });

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
