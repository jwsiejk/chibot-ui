// main.js (Zoom-style wiring) — aligns with Toolbar UI and bottom bar
// Build stamp
console.log("UI build ⏱ 2025-08-30-zoom-r1");

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

/* ------------------------------- Helpers -------------------------------- */
const pick = (...ids) => ids.map(id => document.getElementById(id)).find(Boolean);
const BTN_START = pick("zStart", "btnStart");
const BTN_AUDIO = pick("zAudio", "btnMic");
const BTN_END   = pick("zEnd", "btnDisconnect");
const BTN_CHAT  = pick("zChat", "btnChat");

const CHIP_SRC   = "/static/chip/img/chip.png";
const MOUTH_BASE = "/static/chip/img/visemes";

function normalizeMouthFile(name) {
  if (!name) return "mouth_neutral.png";
  if (/^neutral(\.png)?$/i.test(name)) return "mouth_neutral.png";
  if (!/\.(png|webp|svg)$/i.test(name)) name += ".png";
  return name;
}
function setMouth(name) {
  const img = document.getElementById("chipMouthImg");
  if (!img) return;
  img.src = `${MOUTH_BASE}/${normalizeMouthFile(name)}`;
}
function calibrateMouth() {
  const chip  = document.getElementById("chipImage");
  const mouth = document.getElementById("chipMouthImg");
  if (!chip || !mouth) return;
  // simple center calibration using CSS variables from :root
  mouth.style.top  = getComputedStyle(document.documentElement).getPropertyValue("--mouth-top-pct") || "62%";
  mouth.style.left = getComputedStyle(document.documentElement).getPropertyValue("--mouth-left-pct") || "50%";
}
function rehydrateChip() {
  const chip  = document.getElementById("chipImage");
  if (!chip) return;
  chip.classList.remove("hidden");
  chip.style.display    = "block";
  chip.style.visibility = "visible";
  chip.style.opacity    = "1";
  if (!chip.getAttribute("src") || chip.getAttribute("src").trim() === "") chip.src = `${CHIP_SRC}?v=${Date.now()}`;
  chip.onerror = () => { chip.src = `${CHIP_SRC}?v=${Date.now()}`; chip.classList.remove("hidden"); };
  calibrateMouth();
}

/* --------------------------- Chat lane wiring --------------------------- */
let chatLane = (localStorage.getItem("chatLane") === "text") ? "text" : "live";
const getChatLane = () => chatLane;
const setChatLane = (lane) => { chatLane = (lane === "text") ? "text" : "live"; try { localStorage.setItem("chatLane", chatLane); } catch {} };

// Exclusive chat windows (Text vs Live/TTS)
const ac_chatTextEl = document.getElementById("chatText");
const ac_chatTTSEl  = document.getElementById("chatTTS");
const ac_show = (el) => { if (el) el.classList.remove("hidden"); };
const ac_hide = (el) => { if (el) el.classList.add("hidden"); };
const ac_visible = (el) => !!(el && !el.classList.contains("hidden"));

const ac_openOnlyChat = (which) => {
  if (which === "text") {
    ac_show(ac_chatTextEl); ac_hide(ac_chatTTSEl);
    try { ac_chatTextEl?.querySelector("input,textarea")?.focus(); } catch {}
  } else { ac_show(ac_chatTTSEl); ac_hide(ac_chatTextEl); }
};
const ac_toggleChat = (which) => {
  if (which === "text") { if (ac_visible(ac_chatTextEl)) ac_hide(ac_chatTextEl); else ac_openOnlyChat("text"); }
  else { if (ac_visible(ac_chatTTSEl)) ac_hide(ac_chatTTSEl); else ac_openOnlyChat("live"); }
};

/* ------------------------------- Boot UI -------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  try { setToolbarHeightVar(); } catch {}
  rehydrateChip(); requestAnimationFrame(rehydrateChip); setTimeout(rehydrateChip, 250);

  // Bottom bar wiring
  BTN_CHAT && BTN_CHAT.addEventListener("click", () => ac_toggleChat(getChatLane()));
  BTN_START && BTN_START.addEventListener("click", onStartClicked);
  BTN_AUDIO && BTN_AUDIO.addEventListener("click", onAudioClicked);
  BTN_END   && BTN_END.addEventListener("click", endSession);

  // App menu
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
function setSessionActive(on) {
  sessionActive = !!on;
  if (BTN_START) BTN_START.disabled = sessionActive;
  if (BTN_END)   BTN_END.disabled   = !sessionActive;
}

const setStatus = (t) => { const el = document.getElementById("statusBanner"); if (el) el.textContent = t || ""; };

// Mic UI / VAD visuals
setMicUIUpdater((on, recording = false) => {
  if (!BTN_AUDIO) return;
  BTN_AUDIO.classList.toggle("primary", !!on);
  BTN_AUDIO.classList.toggle("recording", !!recording);
  let label = "Audio";
  if (recording) label = "Recording…";
  else if (on)   label = "Listening";
  BTN_AUDIO.querySelector("span:last-child")?.replaceChildren(document.createTextNode(label));
});

setVoiceGuide((text) => _chipGuide(text));
setRecordCallbacks(
  async () => { /* onStartRecording no-op */ },
  async () => {
    await _vm_stopRecording(async (blob, durMs) => { await handleVoiceOnceResponse({ blob, durMs }); });
  }
);

setArmVADForSend(() => _vm_armVAD());
wireChatLane(getChatLane, setChatLane);
updateChatButtonLabel(getChatLane());

wireChatMenu(getChatLane, (lane) => {
  setChatLane(lane); updateChatButtonLabel(getChatLane());
  ac_openOnlyChat(getChatLane() === "text" ? "text" : "live");
}, () => {});

// Suggestions -> send
setRenderSuggestions((sugs) => _chipRenderSuggestions(sugs, (s) => {
  if (/end chat/i.test(s)) { _chipEndConversation(); return; }
  sendChat(s);
}));

/* ------------------------------ Handlers -------------------------------- */
async function onStartClicked() {
  if (sessionActive) return;
  const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;

  setSessionActive(TrueFalse(true));
  setStatus("Connecting");
  _chipGuide("Starting");
  await startDynamicSession();

  // Auto-arm VAD
  _chipGuide("Now listening — start talking after the tone.");
  try { await _vm_armVAD(); } catch (e) { console.warn("VAD arm failed", e); }

  // If silence, nudge
  setTimeout(() => {
    const armed = BTN_AUDIO?.classList.contains("primary");
    if (armed) _chipGuide("I didn’t catch anything—check your mic or tap Audio to try again.");
  }, 8000);
}

async function onAudioClicked() {
  // toggle VAD
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

function TrueFalse(v){ return !!v; } // tiny util to keep setSessionActive readable

function endSession() {
  try {
    _chipStep("disconnect", "teardown");
    _vm_disarmVAD();
    _vm_stopPlayback();
    _chipClearIdleNudge();
    _chipSetState("idle");
    setSessionActive(FalseTrue(false));
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipGuide("Disconnected. Press Start to begin a new session.");
  } catch (e) { console.warn("disconnect error", e); }
}
function FalseTrue(v){ return !!v; }

/* ---------------------------- Boot experience --------------------------- */
// Auth wiring
wireLoginAndProfileHandlers();

(async () => {
  const g = await gate({ applyLayout: true });
  if (g && g.ok) {
    setStatus("Disconnected. Press Start to begin a new session.");
    _chipGuide("Press Start to speak with Chip.");
    _chipStep("boot", "ready");
  } else {
    // Keep layout visible but chat panel hidden pre-login
    const chatPanel = document.getElementById("chatPanel");
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
      setSessionActive(FalseTrue(false));
      return;
    }

    const audioUrl = data && data.audio;
    const reply    = data && data.reply;

    if (typeof reply === "string" && reply.trim()) appendMessage("chip", reply);
    if (audioUrl) { try { await tryPlayWithMouth(audioUrl); } catch (e) { console.warn("Greet audio failed", e); } }

    const chatPanel = document.getElementById("chatPanel");
    if (chatPanel) chatPanel.hidden = false;

    const chatInput = document.getElementById("chatInput");
    if (chatInput) { chatInput.placeholder = "Ask me anything about Pure Storage…"; try { chatInput.focus(); } catch {} }

    _chipStartWaitingCountdown();
    _chipSetState("idle");
    _chipStep("greet", "ready");
  } catch (e) {
    console.warn("startDynamicSession error:", e);
    _chipSetState("idle");
    setSessionActive(FalseTrue(false));
  }
}

// Chat compose
document.getElementById("chatSendBtn")?.addEventListener("click", () => {
  const input = document.getElementById("chatInput"); if (!input) return;
  const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; }
});
document.getElementById("chatInput")?.addEventListener("keydown", (e) => {
  const input = document.getElementById("chatInput"); if (!input) return;
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const val = input.value; if (val && val.trim()) { sendChat(val); input.value = ""; } }
});
