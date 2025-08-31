// main.js — Zoom-style with Text/Live lane picker; chat log always visible
console.log("UI build ⏱ 2025-08-30-lanes-r2");

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

/* ------------------------------- Elements -------------------------------- */
const pick = (id) => document.getElementById(id);
const BTN_START = pick("zStart");
const BTN_AUDIO = pick("zAudio");
const BTN_END   = pick("zEnd");
const BTN_CHAT  = pick("zChat");
const CHAT_MENU = pick("zChatMenu");
const BADGE     = pick("laneBadge");

const chatPanel   = pick("chatPanel");
const chatText    = pick("chatText");
const chatTTS     = pick("chatTTS");
const chipImage   = pick("chipImage");
const chipMouth   = pick("chipMouthImg");

/* ------------------------------- Mouth overlay --------------------------- */
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
function calibrateMouth() {
  if (!chipImage || !chipMouth) return;
  const cs = getComputedStyle(document.documentElement);
  chipMouth.style.top  = cs.getPropertyValue("--mouth-top-pct") || "62%";
  chipMouth.style.left = cs.getPropertyValue("--mouth-left-pct") || "50%";
}
function rehydrateChip() {
  if (!chipImage) return;
  chipImage.classList.remove("hidden");
  chipImage.style.display    = "block";
  chipImage.style.visibility = "visible";
  chipImage.style.opacity    = "1";
  if (!chipImage.getAttribute("src") || chipImage.getAttribute("src").trim() === "") chipImage.src = `${CHIP_SRC}?v=${Date.now()}`;
  chipImage.onerror = () => { chipImage.src = `${CHIP_SRC}?v=${Date.now()}`; chipImage.classList.remove("hidden"); };
  calibrateMouth();
}

/* ------------------------------- Lane state ------------------------------ */
let chatLane = (localStorage.getItem("chatLane") === "live") ? "live" : "text";
const getChatLane = () => chatLane;
const setChatLane = (lane) => {
  chatLane = (lane === "live") ? "live" : "text";
  try { localStorage.setItem("chatLane", chatLane); } catch {}
  refreshLaneUI();
};

function refreshLaneUI() {
  // Chat log (chatText) is ALWAYS visible when chatPanel is open.
  // In Live lane we additionally show the live helper panel.
  if (!chatPanel) return;
  if (!chatPanel.hidden) {
    chatText && chatText.classList.remove("hidden"); // always visible
    if (chatLane === "live") chatTTS && chatTTS.classList.remove("hidden");
    else chatTTS && chatTTS.classList.add("hidden");
  }
  if (BADGE) BADGE.textContent = chatLane === "live" ? "Live" : "Text";
}

/* ------------------------------- Boot UI -------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  try { setToolbarHeightVar(); } catch {}
  rehydrateChip(); requestAnimationFrame(rehydrateChip); setTimeout(rehydrateChip, 200);

  // Bottom bar wiring
  BTN_CHAT && BTN_CHAT.addEventListener("click", onChatToggle);
  BTN_START && BTN_START.addEventListener("click", onStartClicked);
  BTN_AUDIO && BTN_AUDIO.addEventListener("click", onAudioClicked);
  BTN_END   && BTN_END.addEventListener("click", endSession);

  // Lane picker (popover near Chat)
  const openMenu = (ev) => {
    if (!CHAT_MENU) return;
    const rect = BTN_CHAT?.getBoundingClientRect();
    if (rect) {
      CHAT_MENU.style.left = Math.max(12, rect.left + rect.width/2 - 80) + "px";
      CHAT_MENU.style.bottom = "56px";
    }
    CHAT_MENU.classList.remove("hidden");
    ev?.stopPropagation();
  };
  const closeMenu = () => CHAT_MENU && CHAT_MENU.classList.add("hidden");
  BTN_CHAT && BTN_CHAT.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(e); });
  BTN_CHAT && BTN_CHAT.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); openMenu(e);} });
  document.getElementById("laneText")?.addEventListener("click", () => { setChatLane("text"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  document.getElementById("laneLive")?.addEventListener("click", () => { setChatLane("live"); closeMenu(); if (!chatPanel.hidden) refreshLaneUI(); });
  document.addEventListener("click", (e) => { if (CHAT_MENU && !CHAT_MENU.classList.contains("hidden") && !CHAT_MENU.contains(e.target)) closeMenu(); });

  // App menu (Profile/History/Logout)
  const navBtn  = document.getElementById("navMenuBtn");
  const navMenu = document.getElementById("navMenu");
  navBtn && navBtn.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    navMenu.classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (navMenu && !navMenu.classList.contains("hidden") && !navMenu.contains(e.target) && e.target !== navBtn) {
      navMenu.classList.add("hidden");
    }
  });

  document.getElementById("navProfile")?.addEventListener("click", async () => {
    navMenu?.classList.add("hidden");
    setProfileModalMode("edit"); await loadProfileIntoForm(); show(document.getElementById("profileModal"), "flex");
  });
  document.getElementById("navHistory")?.addEventListener("click", async () => {
    navMenu?.classList.add("hidden");
    const { ok, data } = await j("/history", { method:"POST", body:"{}" });
    if (ok && data?.response) appendMessage("chip", data.response);
  });
  document.getElementById("navLogout")?.addEventListener("click", async () => {
    navMenu?.classList.add("hidden");
    try { await fetch("/logout", { method:"POST", credentials:"include" }); } catch {}
    location.reload();
  });
});

/* ------------------------------ Session UX ------------------------------ */
let sessionActive = false;
const setSessionActive = (on) => {
  sessionActive = !!on;
  if (BTN_START) BTN_START.disabled = sessionActive;
  if (BTN_END)   BTN_END.disabled   = !sessionActive;
};
const setStatus = (t) => { const el = $("statusBanner"); if (el) el.textContent = t || ""; };

// Mic UI / VAD visuals
setMicUIUpdater((on, recording = false) => {
  if (!BTN_AUDIO) return;
  BTN_AUDIO.classList.toggle("primary", !!on);
  BTN_AUDIO.classList.toggle("recording", !!recording);
  const labelNode = BTN_AUDIO.querySelector("span:last-child");
  if (labelNode) labelNode.textContent = recording ? "Recording…" : (on ? "Listening" : "Audio");
});

setVoiceGuide((text) => _chipGuide(text));
setRecordCallbacks(
  async () => { /* onStartRecording */ },
  async () => {
    await _vm_stopRecording(async (blob, durMs) => { await handleVoiceOnceResponse({ blob, durMs }); });
  }
);

setArmVADForSend(() => _vm_armVAD());
wireChatLane(getChatLane, setChatLane);

// Suggestions -> send
setRenderSuggestions((sugs) => _chipRenderSuggestions(sugs, (s) => {
  if (/end chat/i.test(s)) { _chipEndConversation(); return; }
  sendChat(s);
}));

/* ------------------------------ Handlers -------------------------------- */
function onChatToggle() {
  if (!chatPanel) return;
  chatPanel.hidden = !chatPanel.hidden;
  if (!chatPanel.hidden) refreshLaneUI();
}

async function onStartClicked() {
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;

  setSessionActive(true);
  setStatus("Connecting");
  _chipGuide("Starting");
  await startDynamicSession();

  // Auto-arm VAD if Live lane or Audio previously armed
  if (chatLane === "live") {
    _chipGuide("Now listening — start talking after the tone.");
    try { await _vm_armVAD(); } catch (e) { console.warn("VAD arm failed", e); }
  }

  setTimeout(() => {
    const armed = BTN_AUDIO?.classList.contains("primary");
    if (armed) _chipGuide("I didn’t catch anything—check your mic or tap Audio to try again.");
  }, 8000);
}

async function onAudioClicked() {
  const armed = BTN_AUDIO?.classList.contains("primary");
  if (armed) { _vm_disarmVAD(); return; }
  const okGate = await gate(); if (!okGate.ok) return;
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

function endSession() {
  try {
    _chipStep("disconnect", "teardown");
    _vm_disarmVAD();
    _vm_stopPlayback();
    _chipClearIdleNudge();
    _chipSetState("idle");
    setSessionActive(false);
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipGuide("Disconnected. Press Start to begin a new session.");
  } catch (e) { console.warn("disconnect error", e); }
}

/* ---------------------------- Boot experience --------------------------- */
import { setProfileModalMode, loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";
wireLoginAndProfileHandlers();

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

// Always ensure mouth overlay stays calibrated on resize
window.addEventListener("resize", calibrateMouth);

/* -------------------------- Dynamic greet / start ----------------------- */
async function startDynamicSession() {
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
    if (audioUrl) { try { await tryPlayWithMouth(audioUrl); } catch (e) { console.warn("Greet audio failed", e); } }

    if (chatPanel) chatPanel.hidden = false;
    refreshLaneUI(); // ensure the right subpanel is visible
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

/* ---------------------------- Chat compose ------------------------------ */
$("chatSendBtn")?.addEventListener("click", () => {
  const input = $("chatInput"); if (!input) return;
  const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; }
});
$("chatInput")?.addEventListener("keydown", (e) => {
  const input = $("chatInput"); if (!input) return;
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; } }
});
