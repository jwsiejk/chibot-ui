import { API } from "./config.js";
import { STATES, setState, getState, onState } from "./state.js";
import { showError, hideError } from "./errors.js";
import { renderSuggestions } from "./suggestions.js";
import { playStream, stopPlayback, setVisemeCallback, isPlaying } from "./audio.js";
import { armVAD, disarmVAD, initMic, speechStart, speechEnd } from "./voice.js";
import { bindControls, openWS, closeWS, sendInterrupt, cancelNudge } from "./ws.js";

const $ = (s) => document.querySelector(s);

let startBtn, endBtn, chatOpenBtn, composer, sendBtn, stateDots, instructionStrip;

document.addEventListener("DOMContentLoaded", async () => {
  startBtn = $("#startButton");
  endBtn = $("#endButton");
  chatOpenBtn = $("#chatButton");
  composer = $("#composerInput");
  sendBtn = $("#composerSend");
  stateDots = $("#stateDots");
  instructionStrip = $("#instructionStrip");

  bindControls(startBtn, endBtn);
  setVisemeCallback(onViseme);

  wireUI();
  renderSuggestions(["Show roadmap", "Explain Portworx", "Demo FlashArray", "Open Admin"], onSuggestion);

  // initial state
  setState(STATES.READY);
  updateStateDots(STATES.READY);
});

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
    updateStateDots(next);
  });
}

async function onStart(){
  hideError();
  try{
    openWS();
    await greet();
    await initMic().catch(()=>{});
    setState(STATES.LISTENING);
    // Auto-open chat UI if you have a collapsible pane
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
  }catch(e){}
}

async function onSend(){
  cancelNudge();
  const text = composer?.value?.trim();
  if (!text) return;
  composer.value = "";
  setState(STATES.THINKING);
  try{
    const r = await fetch(API.CHAT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ text })
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // Server will stream via WS; no-op here
  }catch(e){
    showError(API.CHAT, e.status || "ERR", e.message || "");
  }
}

async function greet(){
  const r = await fetch(API.GREET, { method: "GET", credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function updateStateDots(state){
  const nodes = stateDots?.querySelectorAll?.("[data-dot]") || [];
  nodes.forEach(n => n.classList.toggle("on", n.dataset.dot === state));
}

function onViseme(v){
  // hook for your 2D mouth animation
}
function onSuggestion(text){
  composer.value = text;
  onSend();
}