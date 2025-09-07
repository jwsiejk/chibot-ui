/**
 * app.js — Ask Chip main UI glue
 * Updated: 2025-09-07
 *
 * Fixes:
 *  - Start button is enabled based on /api/v1/auth/me (profile_complete).
 *  - onStart reliably opens WS and greets.
 *  - onSend ensures WS is open before POST /api/v1/chat (prevents “user bubble only”).
 */

import { API } from "./config.js";
import { STATES, setState, getState, onState } from "./state.js";
import { showError, hideError } from "./errors.js";
import { renderSuggestions } from "./suggestions.js";
import { playStream, stopPlayback, setVisemeCallback, isPlaying } from "./audio.js";
import { armVAD, disarmVAD, initMic } from "./voice.js";
import { bindControls, openWS, closeWS, sendInterrupt, cancelNudge, waitWSOpen } from "./ws.js";
import { getSID } from "./util/sid.js";

/* -------------------------------------------------------
   Lightweight CSRF helper
------------------------------------------------------- */
async function ensureCSRF(){
  try{
    let tok = sessionStorage.getItem("csrf");
    if (tok) return tok;
    const r = await fetch("/api/v1/auth/csrf", { credentials: "include" });
    if (!r.ok) return null;
    const j = await r.json();
    tok = j?.csrf || j?.token || null;
    if (tok) sessionStorage.setItem("csrf", tok);
    return tok;
  }catch{ return null; }
}

/* -------------------------------------------------------
   DOM references
------------------------------------------------------- */
const $ = (s) => document.querySelector(s);
let startBtn, endBtn, sendBtn, composer, stateLabelEl;

/* -------------------------------------------------------
   UI helpers
------------------------------------------------------- */
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

function setStartEnabled(enabled){
  try{
    if (startBtn) startBtn.disabled = !enabled;
  }catch{}
}

async function whoAmI(){
  try{
    const r = await fetch("/api/v1/auth/me", { credentials: "include" });
    if (!r.ok) return null;
    return await r.json();
  }catch{ return null; }
}

/* -------------------------------------------------------
   Greet and flow wiring
------------------------------------------------------- */
async function greet(){
  const sid = getSID();
  const r = await fetch(`${API.GREET}?session_id=${encodeURIComponent(sid)}`, { credentials: "include" });
  if (!r.ok) throw new Error(`greet HTTP ${r.status}`);
}

async function onStart(){
  hideError();
  try{
    // Open socket first (so greet + subsequent frames can stream)
    openWS();
    await waitWSOpen();               // throws on timeout; caught below

    // Trigger greeting (TTS kicked by your app after greet returns)
    await greet();

    // Prepare mic for first turn
    try{
      await initMic();
    }catch{
      showError("mic","blocked","Microphone permission denied");
    }

    setState(STATES.LISTENING);
    document.body.classList.add("chat-open");
  }catch(e){
    showError(API.GREET, e.status || "ERR", e.message || "start failed");
    setStartEnabled(true);            // don’t leave the user stuck
  }
}

async function onEnd(){
  try{
    closeWS();
  }catch{}
  setState(STATES.READY);
  document.body.classList.remove("chat-open");
}

/* -------------------------------------------------------
   Send message (user turn)
------------------------------------------------------- */
async function onSend(){
  // Pre-append the user bubble so it feels snappy
  try {
    const vtmp = (composer?.value || '').trim();
    if(vtmp) { try{ addChatMessage('user', vtmp); }catch(e){} }
  } catch(e) {}

  cancelNudge();
  const text = (composer?.value || "").trim();
  if (!text) return;
  composer.value = "";

  try{
    // Make sure WS is ready to receive frames for the turn
    try { openWS(); await waitWSOpen(); } catch(e) {}

    const tok = await ensureCSRF();
    const r = await fetch(API.CHAT, {
      method: "POST",
      headers: { "Content-Type":"application/json", ...(tok ? {"X-CSRF-Token":tok} : {}) },
      credentials: "include",
      body: JSON.stringify({ text, session_id: getSID() })
    });
    if (!r.ok) {
      showError(API.CHAT, r.status, "chat failed");
      return;
    }
    // Server responds with { ok: true, turn_id }, frames will stream via WS
  }catch(e){
    showError(API.CHAT, "ERR", e?.message || "send failed");
  }
}

/* -------------------------------------------------------
   Nudge and state dots (optional wiring)
------------------------------------------------------- */
function updateStateIndicatorsOnce(s){ /* keep your existing UI dots if any */ }

/* -------------------------------------------------------
   Bootstrapping
------------------------------------------------------- */
async function init(){
  // Cache DOM
  startBtn   = $("#startBtn")   || document.getElementById("startBtn");
  endBtn     = $("#endBtn")     || document.getElementById("endBtn");
  sendBtn    = $("#sendBtn")    || document.getElementById("sendBtn");
  composer   = $("#composer")   || document.getElementById("composer");
  stateLabelEl = $("#stateLabel");

  // Wire buttons
  if (startBtn)  startBtn.addEventListener("click", onStart);
  if (endBtn)    endBtn.addEventListener("click", onEnd);
  if (sendBtn)   sendBtn.addEventListener("click", onSend);
  if (composer)  composer.addEventListener("keydown", (e)=>{ if(e.key==="Enter" && !e.shiftKey){ e.preventDefault(); onSend(); }});

  // WS button states
  bindControls(startBtn, endBtn);

  // Decide Start enabled based on profile gate
  try{
    const me = await whoAmI();
    const complete = !!(me && me.ok && me.profile_complete);
    // If profile gate is on and not complete, keep disabled; otherwise enable.
    setStartEnabled(complete || me === null /* fail-open if whoAmI failed */);
    // If you want a visible note when profile isn’t complete, add it here.
  }catch{
    // Fail-open rather than blocking; server’s /greet will enforce gate
    setStartEnabled(true);
  }

  // Optional: preload CSRF so the first POST is fast
  try{ await ensureCSRF(); }catch{}
}

document.addEventListener("DOMContentLoaded", init);

/* -------------------------------------------------------
   Expose for other modules if needed
------------------------------------------------------- */
export { onStart, onEnd, onSend, addChatMessage };
