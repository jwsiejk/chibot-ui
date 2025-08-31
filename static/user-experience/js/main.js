// main.js — Zoom-style, menu on top-right, centered bottom bar, robust init, live-visible chat
console.log("UI build ⏱ 2025-08-31-ui-r1");

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

/* ---------------------- Robust init (bind handlers always) --------------- */
function onReady(fn){
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn, { once:true });
  else fn();
}

/* ------------------------------- Elements -------------------------------- */
const el = (id) => document.getElementById(id);
let BTN_START, BTN_AUDIO, BTN_END, BTN_CHAT, CHAT_MENU, BADGE;
let chatPanel, chatText, chatTTS, chipImage, chipMouth;

/* -------------------------------- Lanes ---------------------------------- */
let chatLane = (localStorage.getItem("chatLane") === "live") ? "live" : "text";
const getChatLane = () => chatLane;
const setChatLane = (lane) => {
  chatLane = (lane === "live") ? "live" : "text";
  try { localStorage.setItem("chatLane", chatLane); } catch {}
  refreshLaneUI();
};

/* ------------------------- Mouth overlay / calibration ------------------- */
const CHIP_SRC   = "/static/chip/img/chip.png";
const MOUTH_BASE = "/static/chip/img/visemes";

function normalizeMouthFile(name) {
  if (!name) return "mouth_neutral.png";
  if (/^neutral(\.png)?$/i.test(name)) return "mouth_neutral.png";
  if (!/\.(png|webp|svg)$/i.test(name)) name += ".png";
  return name;
}
function setMouth(name) {
  if (!chipMouth) return;
  chipMouth.src = `${MOUTH_BASE}/${normalizeMouthFile(name)}`;
}

function applyMouthFromStorage(){
  if (!chipMouth) return;
  const top  = localStorage.getItem("mouthTopPct");
  const left = localStorage.getItem("mouthLeftPct");
  const w    = localStorage.getItem("mouthWidthPx");
  if (top)  chipMouth.style.top  = top;
  if (left) chipMouth.style.left = left;
  if (w)    chipMouth.style.width = w;
}

function calibrateMouth() {
  if (!chipImage || !chipMouth) return;
  // First apply any stored calibration
  applyMouthFromStorage();
  // If nothing stored, default to CSS variables
  const cs = getComputedStyle(document.documentElement);
  if (!chipMouth.style.top)  chipMouth.style.top  = cs.getPropertyValue("--mouth-top-pct") || "62%";
  if (!chipMouth.style.left) chipMouth.style.left = cs.getPropertyValue("--mouth-left-pct") || "50%";
  if (!chipMouth.style.width) chipMouth.style.width = cs.getPropertyValue("--mouth-width") || "120px";
}

function enableCalibration(){
  const stage = el("chipStage");
  if (!stage || !chipMouth) return;
  stage.classList.add("calibrating");
  let dragging = false;
  let startX=0, startY=0, startTop=0, startLeft=0;

  const onDown = (ev)=>{
    if (!ev.shiftKey) return; // only with Shift to avoid accidental moves
    dragging = true;
    const rect = stage.getBoundingClientRect();
    startX = ev.clientX; startY = ev.clientY;
    startTop  = parseFloat((chipMouth.style.top||"50%").replace('%',''));
    startLeft = parseFloat((chipMouth.style.left||"50%").replace('%',''));
    ev.preventDefault();
  };
  const onMove = (ev)=>{
    if (!dragging) return;
    const dy = ((ev.clientY - startY) / window.innerHeight) * 100;
    const dx = ((ev.clientX - startX) / window.innerWidth) * 100;
    const t = Math.max(0, Math.min(100, startTop + dy));
    const l = Math.max(0, Math.min(100, startLeft + dx));
    chipMouth.style.top  = t.toFixed(2) + "%";
    chipMouth.style.left = l.toFixed(2) + "%";
  };
  const onUp = ()=>{
    if (!dragging) return;
    dragging = false;
    localStorage.setItem("mouthTopPct", chipMouth.style.top);
    localStorage.setItem("mouthLeftPct", chipMouth.style.left);
  };
  stage.addEventListener("mousedown", onDown);
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);

  // Double-click mouth to toggle width presets
  chipMouth.addEventListener("dblclick", () => {
    const cur = parseInt(chipMouth.style.width || "120", 10);
    const next = (cur >= 140) ? 120 : (cur >= 120 ? 140 : 120);
    chipMouth.style.width = next + "px";
    localStorage.setItem("mouthWidthPx", chipMouth.style.width);
  });
}

/* ----------------------------- Wiring / boot ----------------------------- */
function initUI(){
  // Cache elements
  BTN_START = el("zStart"); BTN_AUDIO = el("zAudio"); BTN_END = el("zEnd"); BTN_CHAT = el("zChat");
  CHAT_MENU = el("zChatMenu"); BADGE = el("laneBadge");
  chatPanel = el("chatPanel"); chatText = el("chatText"); chatTTS = el("chatTTS");
  chipImage = el("chipImage"); chipMouth = el("chipMouthImg");

  // Layout & chip
  try { setToolbarHeightVar(); } catch {}
  rehydrateChip(); requestAnimationFrame(rehydrateChip); setTimeout(rehydrateChip, 200);

  // Bind bottom bar (works regardless of DOMContentLoaded timing)
  BTN_CHAT && BTN_CHAT.addEventListener("click", onChatToggle);
  BTN_START && BTN_START.addEventListener("click", onStartClicked);
  BTN_AUDIO && BTN_AUDIO.addEventListener("click", onAudioClicked);
  BTN_END   && BTN_END.addEventListener("click", endSession);

  // Lane picker popover
  const openMenu = (ev) => {
    if (!CHAT_MENU || !BTN_CHAT) return;
    const rect = BTN_CHAT.getBoundingClientRect();
    CHAT_MENU.style.left = Math.max(12, rect.left + rect.width/2 - 80) + "px";
    CHAT_MENU.style.bottom = (window.innerHeight - rect.top + 10) + "px";
    CHAT_MENU.classList.remove("hidden");
    ev?.stopPropagation();
  };
  const closeMenu = () => CHAT_MENU && CHAT_MENU.classList.add("hidden");
  BTN_CHAT && BTN_CHAT.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(e); });
  BTN_CHAT && BTN_CHAT.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); openMenu(e);} });
  el("laneText")?.addEventListener("click", () => { setChatLane("text"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  el("laneLive")?.addEventListener("click", () => { setChatLane("live"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  document.addEventListener("click", (e) => { if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  // App menu (top-right)
  const navBtn  = el("navMenuBtn");
  const navMenu = el("navMenu");
  navBtn && navBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); navMenu.classList.toggle("hidden"); });
  document.addEventListener("click", (e) => { if (navMenu && !navMenu.classList.contains("hidden") && !navMenu.contains(e.target) && e.target !== navBtn) navMenu.classList.add("hidden"); });

  el("navProfile")?.addEventListener("click", async () => { navMenu?.classList.add("hidden"); setProfileModalMode("edit"); await loadProfileIntoForm(); show(el("profileModal"), "flex"); });
  el("navHistory")?.addEventListener("click", async () => { navMenu?.classList.add("hidden"); const { ok, data } = await j("/history", { method:"POST", body:"{}" }); if (ok && data?.response) appendMessage("chip", data.response); });
  el("navLogout")?.addEventListener("click", async () => { navMenu?.classList.add("hidden"); try { await fetch("/logout", { method:"POST", credentials:"include" }); } catch {} location.reload(); });

  // Chat compose
  el("chatSendBtn")?.addEventListener("click", () => {
    const input = el("chatInput"); if (!input) return;
    const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; }
  });
  el("chatInput")?.addEventListener("keydown", (e) => {
    const input = el("chatInput"); if (!input) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; } }
  });

  // Mic UI
  setMicUIUpdater((on, recording = false) => {
    if (!BTN_AUDIO) return;
    BTN_AUDIO.classList.toggle("primary", !!on);
    BTN_AUDIO.classList.toggle("recording", !!recording);
    const labelNode = BTN_AUDIO.querySelector("span:last-child");
    if (labelNode) labelNode.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
  });
  setVoiceGuide((text) => _chipGuide(text));
  setRecordCallbacks(async () => {}, async () => {
    await _vm_stopRecording(async (blob, durMs) => { await handleVoiceOnceResponse({ blob, durMs }); });
  });
  setArmVADForSend(() => _vm_armVAD());
  wireChatLane(getChatLane, setChatLane);
  setTimeout(refreshLaneUI, 0);

  // Calibration UX
  calibrateMouth();
  // Toggle calibration mode with double click on stage background
  el("chipStage")?.addEventListener("dblclick", (e) => {
    if (e.target === chipMouth) return; // mouth dblclick handled separately
    const st = el("chipStage"); if (!st) return;
    if (st.classList.contains("calibrating")) { st.classList.remove("calibrating"); }
    else { enableCalibration(); }
  });
}

/* ------------------------------ Session UX ------------------------------ */
let sessionActive = false;
const setSessionActive = (on) => {
  sessionActive = !!on;
  if (BTN_START) BTN_START.disabled = sessionActive;
  if (BTN_END)   BTN_END.disabled   = !sessionActive;
};
const setStatus = (t) => { const elx = $("statusBanner"); if (elx) elx.textContent = t || ""; };

function onChatToggle(){
  if (!chatPanel) return;
  chatPanel.hidden = !chatPanel.hidden;
  if (!chatPanel.hidden) refreshLaneUI();
}

async function onStartClicked(){
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return; // auth/profile gating (modal if needed) fileciteturn1file0
  setSessionActive(true);
  setStatus("Connecting");
  _chipGuide("Starting");
  await startDynamicSession();
  if (chatLane === "live") {
    _chipGuide("Now listening — start talking after the tone.");
    try { await _vm_armVAD(); } catch (e) { console.warn("VAD arm failed", e); }
  }
  setTimeout(() => {
    const armed = BTN_AUDIO?.classList.contains("primary");
    if (armed) _chipGuide("I didn’t catch anything—check your mic or tap Audio to try again.");
  }, 8000);
}

async function onAudioClicked(){
  const armed = BTN_AUDIO?.classList.contains("primary");
  if (armed) { _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate.ok) return; // auth/profile gating (modal if needed) fileciteturn1file0
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    setRecordStream(s);
  } catch (err) {
    console.warn("getUserMedia failed:", err);
    _chipGuide("I can’t access your mic. Check browser permissions and try again.");
    return;
  }
  _vm_stopPlayback(); // barge-in
  await _vm_armVAD();
}

function endSession(){
  try{
    _chipStep("disconnect", "teardown");
    _vm_disarmVAD(); _vm_stopPlayback(); _chipClearIdleNudge(); _chipSetState("idle");
    setSessionActive(false);
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipGuide("Disconnected. Press Start to begin a new session.");
  } catch(e){ console.warn("disconnect error", e); }
}

/* ---------------------------- Boot experience --------------------------- */
wireLoginAndProfileHandlers(); // wires login/profile flows (modal when needed) fileciteturn1file0
onReady(initUI);

(async () => {
  const g = await gate({ applyLayout: true });
  if (g && g.ok) {
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipGuide("Press Start to speak with Chip.");
    _chipStep("boot", "ready");
  } else {
    if (chatPanel) chatPanel.hidden = true;
  }
})();

// Ensure mouth stays aligned on resize
window.addEventListener("resize", calibrateMouth);

/* -------------------------- Dynamic greet / start ----------------------- */
async function startDynamicSession(){
  try {
    _chipSetState("greeting");
    _chipStep("POST /greet →", {});
    const { ok, data, status } = await j("/greet", { method:"POST", body: JSON.stringify({}) });
    if (!ok) {
      _chipStep("greet-failed", { status });
      _chipSetState("idle");
      _chipGuide("Couldn’t start the greeting. Try again?");
      setSessionActive(false);
      return;
    }
    const audioUrl = data && data.audio;
    const reply    = data && data.reply;
    if (typeof reply === "string" && reply.trim()) appendMessage("chip", reply);
    if (audioUrl) { try { await tryPlayWithMouth(audioUrl); } catch(e){ console.warn("Greet audio failed", e); } }
    if (chatPanel) chatPanel.hidden = false;
    refreshLaneUI();
    const chatInput = $("chatInput");
    if (chatInput) { chatInput.placeholder = "Ask me anything about Pure Storage…"; try { chatInput.focus(); } catch {} }
    _chipStartWaitingCountdown();
    _chipSetState("idle");
    _chipStep("greet", "ready");
  } catch (e) {
    console.warn("startDynamicSession error:", e);
    _chipSetState("idle");
    setSessionActive(false);
  }
}
