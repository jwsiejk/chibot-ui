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
  if (tok) return tok;
  try {
    const r = await fetch("/api/v1/auth/csrf", { credentials: "include" });
    if (!r.ok) return null;
    const j = await r.json();
    tok = j?.token || null;
    if (tok) sessionStorage.setItem("csrf", tok);
    return tok;
  } catch {
    return null;
  }
}

/* -------------------------------------------------------
   Start/End wiring
------------------------------------------------------- */
let startBtn, endBtn, sendBtn, composer, stateLabelEl, stateDotsWrap;

function onViseme(v){
  // mouth anim hook (no-op here unless you wire it)
}

document.addEventListener("DOMContentLoaded", async () => {
  startBtn = $("#startButton");
  endBtn = $("#endButton");
  sendBtn = $("#composerSend");
  composer = $("#composerInput");
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
      sendInterrupt();
      setTimeout(() => setState(STATES.LISTENING), 420);
    }
  });
}

function updateStateIndicators(s){
  const label = ({
    [STATES.READY]: "Ready",
    [STATES.LISTENING]: "Listening",
    [STATES.RESPONDING]: "Responding"
  })[s] || "Ready";
  stateLabelEl.textContent = label;
  // dots visibility already controlled via CSS
}

function onSuggestion(text){
  composer.value = text;
  onSend();
}

async function greet(){
  const sid = getSID();
  const r = await fetch(`${API.GREET}?session_id=${encodeURIComponent(sid)}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

async function onStart(){
  hideError();
  try{
    openWS();                 // opens /ws/v1/chat
    await waitWSOpen();       // ensure server subscription is ready
    await greet();            // GET /api/v1/greet?session_id=SID
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
  try { const vtmp = (composer?.value || '').trim(); if(vtmp) { try{ addChatMessage('user', vtmp); }catch(e){} } } catch(e) {}
  cancelNudge();
  const text = (composer?.value || "").trim();
  if (!text) return;
  composer.value = "";
  try{
    const tok = await ensureCSRF();
    const r = await fetch(API.CHAT, {
      method: "POST",
      headers: { "Content-Type":"application/json", ...(tok ? {"X-CSRF-Token":tok} : {}) },
      credentials: "include",
      body: JSON.stringify({ text, session_id: getSID() })
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  }catch(e){
    showError(API.CHAT, e.status || "ERR", e.message || "send failed");
  }
}

/* -------------------------------------------------------
   UI helpers
------------------------------------------------------- */
function updateStateIndicatorsOnce(s){ updateStateIndicators(s); }

/* global function used above */
function addChatMessage(role, text){
  try{
    const box = document.getElementById('chatMessages');
    if(!box || !text) return;
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }catch(e){}
}
