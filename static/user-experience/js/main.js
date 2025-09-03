// main.js — clean build (WS-first, voice barge-in, single-path greet TTS)
console.log("UI build ⏱ patched-2025-09-03");

import { $, show, hide, setToolbarHeightVar } from "./core/dom.js";
import { j } from "./core/api.js";
import {
  _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep,
  setRenderSuggestions, setArmVADHook, _chipClearIdleNudge
} from "./core/state.js";
import {
  appendMessage, appendActions, _chipRenderSuggestions, updateChatButtonLabel, wireChatMenu
} from "./chat/ui.js";
import {
  sendChat, handleVoiceOnceResponse, wireChatLane, setArmVAD as setArmVADForSend, _chipEndConversation
} from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks } from "./voice/vad.js";
import { _vm_stopRecording, setStream as setRecordStream } from "./voice/record.js";
import { setProfileModalMode, loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";

/* --------------------------- Small utilities ---------------------------- */
const el = (id) => document.getElementById(id);
function onReady(fn){ if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once:true }); else fn(); }
function bindClick(node, handler){
  if (!node) return false;
  node.addEventListener("click", handler);
  node.addEventListener("keydown", function(e){ if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(e); } });
  node.setAttribute("data-bound", "1");
  return true;
}

/* ------------------------------- Elements -------------------------------- */
let BTN_START, BTN_AUDIO, BTN_END, BTN_CHAT, CHAT_MENU, BADGE;
let chatPanel, chatText, chatTTS, chipImage, chipMouth;

/* -------------------------------- Lanes ---------------------------------- */
let chatLane = (function(){
  try { return (localStorage.getItem("chatLane") || "text") === "live" ? "live" : "text"; } catch(e) { return "text"; }
})();
const getChatLane = function(){ return chatLane; };
const setChatLane = function(lane){
  chatLane = lane === "live" ? "live" : "text";
  try { localStorage.setItem("chatLane", chatLane); } catch(e){}
  refreshLaneUI();
};
const badge = function(){ if (BADGE) BADGE.textContent = chatLane === "live" ? "Live" : "Text"; };

/* ------------------------- Mouth overlay / calibration ------------------- */
const CHIP_SRC   = "/static/chip/img/chip.png";
const MOUTH_BASE = "/static/chip/img/visemes";
function normalizeMouthFile(n){
  if (!n) return "mouth_neutral.png";
  if (/^mouth_/.test(n)) return n.endsWith(".png") ? n : (n + ".png");
  if (/\.(png|webp|svg)$/i.test(n)) return n;
  return n + ".png";
}
function setMouth(name){ if (chipMouth) chipMouth.src = MOUTH_BASE + "/" + normalizeMouthFile(name); }
function calibrateMouth(){
  if (!chipImage || !chipMouth) return;
  try {
    const cs = window.getComputedStyle(chipImage);
    if (!chipMouth.style.top)   chipMouth.style.top   = cs.getPropertyValue("--mouth-top")   || "53%";
    if (!chipMouth.style.left)  chipMouth.style.left  = cs.getPropertyValue("--mouth-left")  || "49%";
    if (!chipMouth.style.width) chipMouth.style.width = cs.getPropertyValue("--mouth-width") || "120px";
  } catch(e) {}
}
function rehydrateChip(){
  const chip  = el("chipImage");
  const mouth = el("chipMouthImg");
  if (!chip) return;
  try { chip.classList.remove("hidden"); } catch(e){}
  chip.style.display    = "block";
  chip.style.visibility = "visible";
  chip.style.opacity    = "1";
  try {
    if (!chip.getAttribute("src") || chip.getAttribute("src").trim() === "") {
      chip.src = CHIP_SRC + "?v=" + Date.now();
    }
  } catch(e){}
  chip.onerror = function(){ chip.src = CHIP_SRC + "?v=" + Date.now(); try{ chip.classList.remove("hidden"); }catch(e){} };
  if (mouth) mouth.onerror = function(){ setMouth("mouth_neutral.png"); };
  calibrateMouth();
}
function enableCalibration(){
  const stage = el("chipStage"); if (!stage || !chipMouth) return;
  stage.classList.add("calibrating");
  let dragging=false, startX=0, startY=0, startTop=0, startLeft=0;
  stage.addEventListener("mousedown", function(ev){
    if (!ev.shiftKey) return;
    dragging=true; startX=ev.clientX; startY=ev.clientY;
    startTop=parseFloat((chipMouth.style.top||"50%").replace("%",""));
    startLeft=parseFloat((chipMouth.style.left||"50%").replace("%",""));
    ev.preventDefault();
  });
  stage.addEventListener("mousemove", function(ev){
    if (!dragging) return;
    const dy = ((ev.clientY - startY) / stage.clientHeight) * 100;
    const dx = ((ev.clientX - startX) / stage.clientWidth) * 100;
    chipMouth.style.top  = (startTop + dy) + "%";
    chipMouth.style.left = (startLeft + dx) + "%";
  });
  stage.addEventListener("mouseup", function(){ dragging=false; try {
    localStorage.setItem("mouthTopPct", chipMouth.style.top);
    localStorage.setItem("mouthLeftPct", chipMouth.style.left);
  } catch(e){} });
  chipMouth.addEventListener("dblclick", function(){
    const cur=parseInt(chipMouth.style.width||"120",10);
    const next=(cur>=140)?120:140; chipMouth.style.width=next+"px";
    try { localStorage.setItem("mouthWidthPx", chipMouth.style.width); } catch(e){}
  });
}

/* ----------------------------- Wiring / boot ----------------------------- */
function initUI(){
  BTN_START=el("zStart"); BTN_AUDIO=el("zAudio"); BTN_END=el("zEnd"); BTN_CHAT=el("zChat"); CHAT_MENU=el("zChatMenu"); BADGE=el("laneBadge");
  chatPanel=el("chatPanel"); chatText=el("chatText"); chatTTS=el("chatTTS"); chipImage=el("chipImage"); chipMouth=el("chipMouthImg");

  try { setToolbarHeightVar(); } catch(e){}

  // Chip visuals
  rehydrateChip(); requestAnimationFrame(rehydrateChip); setTimeout(rehydrateChip, 200);

  // Bottom bar wiring
  bindClick(BTN_CHAT, onChatToggle);
  bindClick(BTN_START, onStartClicked);
  bindClick(BTN_AUDIO, onAudioClicked);
  bindClick(BTN_END, endSession);

  // Lane picker (open on right-click or middle-click on Chat)
  function openMenu(ev){
    if (!CHAT_MENU||!BTN_CHAT) return;
    const r=BTN_CHAT.getBoundingClientRect();
    CHAT_MENU.style.left=Math.max(12, r.left+r.width/2-80)+"px";
    CHAT_MENU.style.bottom=(window.innerHeight-r.top+10)+"px";
    CHAT_MENU.classList.remove("hidden");
    ev && ev.preventDefault();
  }
  function closeMenu(){ if (CHAT_MENU) CHAT_MENU.classList.add("hidden"); }
  if (BTN_CHAT){
    BTN_CHAT.addEventListener("contextmenu", function(e){ e.preventDefault(); openMenu(e); });
    BTN_CHAT.addEventListener("auxclick", function(e){ if (e.button===1){ e.preventDefault(); openMenu(e);} });
  }
  bindClick(el("laneText"), function(){ setChatLane("text"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); });
  bindClick(el("laneLive"), function(){ setChatLane("live"); closeMenu(); if (chatPanel && !chatPanel.hidden) refreshLaneUI(); });
  document.addEventListener("click", function(e){ if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  // App menu (top-right)
  const navBtn=el("navMenuBtn"); const navMenu=el("navMenu");
  bindClick(navBtn, function(e){ e.preventDefault(); e.stopPropagation(); if (navMenu) navMenu.classList.toggle("hidden"); });
  document.addEventListener("click", function(e){ if (navMenu && !navBtn.contains(e.target) && e.target!==navBtn) navMenu.classList.add("hidden"); });

  bindClick(el("navProfile"), async function(){ if (navMenu) navMenu.classList.add("hidden"); try { await loadProfileIntoForm(); } catch(e){} show(el("profileModal"), "flex"); });
  bindClick(el("navLogout"), async function(){ if (navMenu) navMenu.classList.add("hidden"); try { await fetch("/logout", { method:"POST", credentials:"include" }); } catch(e){} location.reload(); });

  // Chat compose
  bindClick(el("chatSendBtn"), function(){ const input=el("chatInput"); if (!input) return; const v=input.value; if (v && v.trim()) { sendChat(v); input.value=""; } });
  const chatInputEl = el("chatInput");
  if (chatInputEl) chatInputEl.addEventListener("keydown", function(e){ const input=el("chatInput"); if (!input) return; if (e.key==="Enter" && !e.shiftKey){ e.preventDefault(); const v=input.value; if (v&&v.trim()){ sendChat(v); input.value=""; } } });

  // Mic UI
  setMicUIUpdater(function(on, recording){
    if (!BTN_AUDIO) return;
    BTN_AUDIO.classList.toggle("primary", !!on);
    BTN_AUDIO.classList.toggle("recording", !!recording);
    const lbl = BTN_AUDIO.querySelector("span:last-child"); if (lbl) lbl.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
  });
  setVoiceGuide(function(text){ _chipGuide(text); });

  // Voice barge-in on VAD start; send utterance on stop
  setRecordCallbacks(
    async function onStart(){ try { window.dispatchEvent(new Event("chip:bargein")); } catch(e){} },
    async function onStop(blob, durMs){ try { await handleVoiceOnceResponse({ blob, durMs }); } catch(e){} }
  );

  setArmVADForSend(function(){ _vm_armVAD(); });
  wireChatLane(getChatLane, setChatLane);
  refreshLaneUI();

  // Calibration UX
  calibrateMouth();
  const stage = el("chipStage");
  if (stage) stage.addEventListener("dblclick", function(e){ if (e.target && e.target.id === "chipMouthImg") stage.classList.remove("calibrating"); else enableCalibration(); });

  // Debug helper
  window.__chipDebug = function(){ return {
    build: "patched-2025-09-03",
    bound: {
      start: !!(BTN_START && BTN_START.getAttribute("data-bound")),
      audio: !!(BTN_AUDIO && BTN_AUDIO.getAttribute("data-bound")),
      end:   !!(BTN_END   && BTN_END.getAttribute("data-bound")),
      chat:  !!(BTN_CHAT  && BTN_CHAT.getAttribute("data-bound"))
    }
  }; };
}

function refreshLaneUI(){
  if (!chatPanel) return;
  if (chatText) chatText.classList.remove("hidden"); // always visible
  if (chatLane==="live"){ if (chatTTS) chatTTS.classList.remove("hidden"); }
  else { if (chatTTS) chatTTS.classList.add("hidden"); }
  badge();
}

/* ------------------------------ Session UX ------------------------------ */
let sessionActive=false;
const setSessionActive=function(on){ sessionActive=!!on; if (BTN_START) BTN_START.disabled=on; if (BTN_END) BTN_END.disabled=!on; };
const setStatus=function(t){ const s=$("statusBanner"); if (s) s.textContent=t||""; };

function onChatToggle(){ if (!chatPanel) return; chatPanel.hidden=!chatPanel.hidden; if (!chatPanel.hidden) refreshLaneUI(); }

async function onStartClicked(){
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate || !okGate.ok) return;
  setSessionActive(true);
  setStatus("Connecting…");
  try {
    await startDynamicSession();
    // Optional: auto-arm mic for Live lane
    if (getChatLane() === "live"){
      try { _vm_stopPlayback(); await _vm_armVAD(); } catch(e){ console.warn("VAD arm failed", e); }
    }
    setStatus("Ready");
  } catch(e){
    console.warn("start failed", e);
    setStatus("Ready");
  }
}

async function onAudioClicked(){
  const armed = BTN_AUDIO && BTN_AUDIO.classList.contains("primary");
  if (armed){ _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate || !okGate.ok) return;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    setRecordStream(s);
  } catch(err){
    console.warn("getUserMedia failed:", err);
    _chipGuide("I couldn't access your mic. Check browser permissions and try again.");
    return;
  }
  _vm_stopPlayback();
  await _vm_armVAD(); // barge-in then listen
}

function endSession(){
  try{
    _chipStep("disconnect","teardown");
    _vm_disarmVAD(); _vm_stopPlayback(); _chipClearIdleNudge(); _chipSetState("idle"); setSessionActive(false);
    setStatus("Disconnected. Press Start to begin a new session.");
  } catch(e){ console.warn("disconnect error", e); }
}

/* ---------------------------- Boot experience --------------------------- */
wireLoginAndProfileHandlers();
onReady(initUI);

(async function(){
  const g = await gate({ applyLayout: true });
  if (g && g.ok){
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipStep("boot","ready");
  } else {
    if (chatPanel) chatPanel.hidden=true;
  }
})();

window.addEventListener("resize", calibrateMouth);

/* --------------------------- Greeting sequence --------------------------- */
export async function startDynamicSession(){
  try{
    _chipSetState("greeting");
    _chipStep("GET /api/greet →", {});
    const res = await j("/api/greet");
    const ok  = res && (res.ok !== false);
    const status = res && res.status;
    if (!ok){
      _chipStep("greet-failed",{status: status});
      _chipSetState("idle");
      alert("Could not start the greeting. Try again?");
      setSessionActive(false);
      return;
    }

    // Unified greet text (server only returns text)
    const reply = String(res.reply ?? res.reply_text ?? res.text ?? res.message ?? "").trim();
    if (reply) appendMessage("assistant", reply);

    // SINGLE PATH: always synthesize greet audio via TTS
    if (reply){
      const ttsRes = await fetch("/api/v1/voice/tts-with-visemes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: reply })
      });
      const tts = await ttsRes.json().catch(function(){ return null; });
      if (tts){
        if (tts.audio) {
          try { await tryPlayWithMouth(tts.audio); } catch(e){ console.warn("Greet TTS (url) failed", e); }
        } else if (tts.audio_base64 || tts.audio_b64) {
          try {
            const url = "data:audio/mpeg;base64," + (tts.audio_base64 || tts.audio_b64);
            await tryPlayWithMouth(url);
          } catch(e){ console.warn("Greet TTS (b64) failed", e); }
        }
      }
    }

    if (chatPanel) chatPanel.hidden = false; refreshLaneUI();
    const chatInput = $("chatInput");
    if (chatInput){
      chatInput.placeholder = "Ask me anything about Pure Storage…";
      try { chatInput.focus(); } catch(e){}
    }
    setStatus("Ready");
    _chipSetState("idle");
  } catch(e){
    console.warn("startDynamicSession failed", e);
    setStatus("Ready");
    _chipSetState("idle");
  }
}
