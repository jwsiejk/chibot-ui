import { API } from "./config.js";
import { STATES, setState, getState, onState } from "./state.js";
import { showError, hideError } from "./errors.js";
import { renderSuggestions } from "./suggestions.js";
import { playStream, stopPlayback, setVisemeCallback, isPlaying } from "./audio.js";
import { armVAD, disarmVAD, initMic } from "./voice.js";
import { bindControls, openWS, closeWS, sendInterrupt, cancelNudge, waitWSOpen } from "./ws.js";
import { getSID } from "./util/sid.js";

const $ = (s) => document.querySelector(s);

/* -------------------------------------------------------
   CSRF helpers
------------------------------------------------------- */
async function ensureCSRF(){
  let tok = sessionStorage.getItem("csrf");
  if (!tok) {
    try{
      const r = await fetch("/api/v1/auth/csrf", { credentials: "include" });
      const j = await r.json();
      if (j && j.ok && j.csrf_token) {
        sessionStorage.setItem("csrf", j.csrf_token);
      }
    }catch(e){ /* best-effort */ }
  }
  return {
    "X-CSRF-Token": sessionStorage.getItem("csrf") || ""
  };
}

/* -------------------------------------------------------
   State / UI elements
------------------------------------------------------- */
let startBtn, endBtn, chatOpenBtn, composer, sendBtn, instructionStrip;
let stateLabelEl;
let stateDotsWrap;

document.addEventListener("DOMContentLoaded", async () => {
  startBtn = $("#startButton");
  endBtn = $("#endButton");
  chatOpenBtn = $("#chatButton");
  composer = $("#composerInput");
  sendBtn = $("#composerSend");
  instructionStrip = $("#instructionStrip");
  stateLabelEl = $("#stateLabel");
  stateDotsWrap = $("#stateDots");

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

  // React when state changes to toggle VAD and labels
  onState(({prev, next}) => {
    if (next === STATES.LISTENING){
      armVAD(1);
    } else if (next === STATES.RESPONDING){
      // threshold boost during playback
      armVAD(2);
    } else {
      disarmVAD();
    }
    updateStateIndicators(next);
  });
}

/* -------------------------------------------------------
   Start / End
------------------------------------------------------- */
async function greet(){
  const sid = getSID();
  const r = await fetch(`${API.GREET}?session_id=${encodeURIComponent(sid)}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

async function onStart(){
  hideError();
  try{
    openWS();                 // opens /ws/v1/chat
    await waitWSOpen();      // ensure server subscription is ready
    await greet();           // GET /api/v1/greet?session_id=SID
    await initMic().catch((e)=>{ showError("mic","blocked","Microphone permission denied"); });
    setState(STATES.LISTENING);
    document.body.classList.add("chat-open");
  }catch(e){
    showError(API.GREET, e.status || "ERR", e.message || "start failed");
  }
}

async function onEnd(){
  try{
    closeWS();
    setState(STATES.READY);
    document.body.classList.remove("chat-open");
  }catch(e){ /* noop */ }
}

/* -------------------------------------------------------
   Text send
------------------------------------------------------- */
async function onSend(){
  cancelNudge();
  const text = (composer?.value || "").trim();
  if (!text) return;
  composer.value = "";
  setState(STATES.THINKING);

  try{
    const r = await fetch(API.CHAT, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(await ensureCSRF())
      },
      body: JSON.stringify({ text, session_id: getSID() })
    });
    if (!r.ok){
      showError(API.CHAT, r.status, "chat failed");
      setState(STATES.READY);
    }
  }catch(e){
    showError(API.CHAT, "ERR", e.message || "chat failed");
    setState(STATES.READY);
  }
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
  if (stateDotsWrap){
    stateDotsWrap.querySelectorAll("[data-dot]").forEach(d => d.classList.toggle("on", d.dataset.dot === state));
  }
  if (stateLabelEl) stateLabelEl.textContent = labelForState(state);
}

/* -------------------------------------------------------
   Hooks
------------------------------------------------------- */
function onViseme(v){
  // hook for your 2D mouth animation
}
function onSuggestion(text){
  if (!composer) return;
  composer.value = text;
  onSend();
}
