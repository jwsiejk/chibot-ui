import { API } from "./config.js";
import { STATES, setState, getState, onState } from "./state.js";
import { showError, hideError } from "./errors.js";
import { renderSuggestions } from "./suggestions.js";
import { playStream, stopPlayback, setVisemeCallback, isPlaying } from "./audio.js";
import { armVAD, disarmVAD, initMic, speechStart, speechEnd } from "./voice.js";
import { bindControls, openWS, closeWS, sendInterrupt, cancelNudge } from "./ws.js";
import { getSID } from './util/sid.js';

const $ = (s) => document.querySelector(s);

/* -------------------------------------------------------
   CSRF helpers
------------------------------------------------------- */
async function ensureCSRF(){
  let tok = sessionStorage.getItem("csrf");
  if (!tok) {
    try {
      const r = await fetch("/api/v1/auth/csrf", { credentials: "include" });
      const j = await r.json();
      if (j?.ok && j?.csrf) {
        sessionStorage.setItem("csrf", j.csrf);
        tok = j.csrf;
      }
    } catch (e) { /* ignore */ }
  }
  return tok || "";
}
function csrfHeader(){
  const tok = sessionStorage.getItem("csrf");
  return tok ? { "X-CSRF-Token": tok } : {};
}

/* -------------------------------------------------------
   UI refs
------------------------------------------------------- */
let startBtn, endBtn, chatOpenBtn, composer, sendBtn, instructionStrip;
let stateLabelEl;   // label under the three dots
let stateDotsWrap;  // optional: if you still render [data-dot] dots

document.addEventListener("DOMContentLoaded", async () => {
  startBtn = $("#startButton");
  endBtn = $("#endButton");
  chatOpenBtn = $("#chatButton");
  composer = $("#composerInput");
  sendBtn = $("#composerSend");
  instructionStrip = $("#instructionStrip");
  stateLabelEl = $("#stateLabel");
  stateDotsWrap = $("#stateDots"); // present in older templates; harmless if null

  bindControls(startBtn, endBtn);
  setVisemeCallback(onViseme);

  wireUI();
  renderSuggestions(["Show roadmap", "Explain Portworx", "Demo FlashArray", "Open Admin"], onSuggestion);

  // prefetch CSRF so first POSTs don't 403
  await ensureCSRF();

  // initial state
  setState(STATES.READY);
  updateStateIndicators(STATES.READY);
});

/* -------------------------------------------------------
   Event wiring
------------------------------------------------------- */
function wireUI(){
  startBtn?.addEventListener("click", onStart);
  endBtn?.addEventListener("click", onEnd);
  sendBtn?.addEventListener("click", onSend);
  composer?.addEventListener("keydown", (e)=>{ if (e.key === "Enter") onSend(); });

  // Soft barge-in: pause on first VAD hit; confirm ~420ms; commit interrupt
  document.addEventListener("vad-hit", () => {
    if (getState() === STATES.RESPONDING && isPlaying()){
      stopPlayback();
      setTimeout(() => sendInterrupt(), 420);
    }
  });

  onState(({prev, next}) => {
    if (next === STATES.LISTENING){
      armVAD(1);
    } else if (next === STATES.RESPONDING){
      armVAD(2); // threshold boost during playback
    } else {
      disarmVAD();
    }
    updateStateIndicators(next);
  });
}

/* -------------------------------------------------------
   Start / End
------------------------------------------------------- */
async function onStart(){
  hideError();
  try{
    openWS();
    await greet();               // GET /api/v1/greet
    await initMic().catch((e)=>{ showError('mic','blocked','Microphone permission denied'); }); // mic optional
    setState(STATES.LISTENING);
    document.body.classList.add("chat-open"); // show chat if collapsible
  }catch(e){
    showError(API.GREET, e.status || "ERR", e.message || "start failed");
  }
}

async function onEnd(){
  try{
    closeWS();
    setState(STATES.READY);
    document.body.classList.remove("chat-open");
  }catch(e){}
}

/* -------------------------------------------------------
   Text send
------------------------------------------------------- */
async function onSend(){
  cancelNudge();
  const text = composer?.value?.trim();
  if (!text) return;
  composer.value = "";
  setState(STATES.THINKING);
  try{
    await ensureCSRF();
    const headers = Object.assign({ "Content-Type": "application/json" }, csrfHeader());
    const r = await fetch(API.CHAT, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ text, session_id: getSID() })
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // Response streams over WS
  }catch(e){
    showError(API.CHAT, e.status || "ERR", e.message || "");
  }
}

/* -------------------------------------------------------
   Greet (unchanged contract; now JSON)
------------------------------------------------------- */
async function greet(){ const sid = getSID(); const r = await fetch(`${API.GREET}?session_id=${encodeURIComponent(sid)}`, {credentials:'include'}); if(!r.ok) throw new Error(`HTTP ${r.status}`); });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* -------------------------------------------------------
   Indicators (dots + label)
------------------------------------------------------- */
function labelForState(state){
  switch (state){
    case STATES.LISTENING: return "Listening";
    case STATES.THINKING:  return "Thinking";
    case STATES.RESPONDING:return "Responding";
    default:               return "Ready";
  }
}
function updateStateIndicators(state){
  // optional legacy dots toggling if your DOM has [data-dot]
  const nodes = stateDotsWrap?.querySelectorAll?.("[data-dot]") || [];
  nodes.forEach(n => n.classList.toggle("on", n.dataset.dot === state));

  // authoritative label under the three dots
  if (stateLabelEl) stateLabelEl.textContent = labelForState(state);
}

/* -------------------------------------------------------
   Hooks
------------------------------------------------------- */
function onViseme(v){
  // hook for your 2D mouth animation
}
function onSuggestion(text){
  composer.value = text;
  onSend();
}
