// main.js — imports, wiring, boot sequence
import { $, show, hide, setToolbarHeightVar, _getQueryParam } from "./core/dom.js";
import { _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep, setRenderSuggestions, setArmVADHook, _chipScheduleIdleNudge, _chipClearIdleNudge } from "./core/state.js";
import { j } from "./core/api.js";
import { setProfileModalMode, loadProfileIntoForm, applyAuthedLayout, enforceProfileCompleteness, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";
import { appendMessage, appendActions, _chipRenderSuggestions, updateChatButtonLabel, toggleChatMenu, wireChatMenu } from "./chat/ui.js";
import { sendChat, _limitWords, _isEndTrigger, handleVoiceOnceResponse, wireChatLane, setArmVAD as setArmVADForSend, _chipEndConversation } from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks } from "./voice/vad.js";
import { _vm_startRecording, _vm_stopRecording, setStream as setRecordStream } from "./voice/record.js";

// ---- Static audio files ----
const STATIC_AUDIO_BASE = "/static/chip/audio/";
const GREETING_FILES = ["greeting-static.mp3", "greeting.mp3", "Greeting.mp3"];

// Keep a handle for any active response stream (for disconnect/teardown)
let respRelease = null;

// Chat lane persisted
let chatLane = (localStorage.getItem("chatLane") === "text") ? "text" : "live";
const getChatLane = () => chatLane;
const setChatLane = (lane) => { chatLane = (lane === "text") ? "text" : "live"; try { localStorage.setItem("chatLane", chatLane); } catch {} };

window.addEventListener("load", setToolbarHeightVar);
window.addEventListener("resize", setToolbarHeightVar);
window.addEventListener("orientationchange", setToolbarHeightVar);

document.addEventListener("DOMContentLoaded", () => {
  // Expose suggestion renderer to state (for idle nudges)
  setRenderSuggestions((sugs) => _chipRenderSuggestions(sugs, (s) => {
    if (/end chat/i.test(s)) { _chipEndConversation(); return; }
    sendChat(s);
  }));

  // Chat lane wiring
  wireChatLane(getChatLane, setChatLane);
  setArmVADForSend(() => _vm_armVAD());
  updateChatButtonLabel(getChatLane());
  wireChatMenu(getChatLane, setChatLane, () => { /* no-op */ });

  // Voice UI hooks
  setMicUIUpdater((on, recording=false) => {
    const btnMic = $("btnMic");
    if (!btnMic) return;
    btnMic.classList.toggle("armed", !!on);
    btnMic.classList.toggle("recording", !!recording);
    if (recording) btnMic.textContent = "🎙️ Recording… (tap to stop)";
    else if (on)   btnMic.textContent = "🎤 Listening…";
    else           btnMic.textContent = "🎤 Mic";
  });
  setVoiceGuide((text) => _chipGuide(text));
  setRecordCallbacks(
    async () => {
      // onStartRecording
    },
    async () => {
      // onStopRecording
      await _vm_stopRecording(async (blob, durMs) => {
        await handleVoiceOnceResponse({ blob, durMs });
      });
    }
  );

  // Arm VAD hook used by state transitions
  setArmVADHook(async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    await navigator.mediaDevices.getUserMedia({ audio: true }).then((s)=> setRecordStream(s));
    _vm_stopPlayback(); // barge-in
    await _vm_armVAD();
  });

  // Toolbar: start sessions
  $("btnStatic")?.addEventListener("click", async () => {
    const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;
    document.documentElement.setAttribute("data-chip-mode", "static");
    $("btnModeStatic")?.classList.add("mode-active");
    $("btnModeDynamic")?.classList.remove("mode-active");
    _chipGuide("Press Start or Chat to speak with Chip.");
    _chipSetState("idle");
    await startStaticSession();
  });

  $("btnDynamic")?.addEventListener("click", async () => {
    const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;
    document.documentElement.setAttribute("data-chip-mode", "dynamic");
    $("btnModeDynamic")?.classList.add("mode-active");
    $("btnModeStatic")?.classList.remove("mode-active");
    await startDynamicSession();
  });

  // Mic button
  $("btnMic")?.addEventListener("click", async () => {
    // Stop early if recording
    // (record.js handles onComplete callback)
    // We only toggle VAD here
    const btnMic = $("btnMic");
    if (btnMic?.classList.contains("recording")) {
      // record.js will call our onStopRecording path
      return;
    }
    // Toggle armed
    const armed = btnMic?.classList.contains("armed");
    if (armed) { _vm_disarmVAD(); return; }
    await _vm_armVAD();
  });

  // Chat compose
  $("chatSendBtn")?.addEventListener("click", () => {
    const input = $("chatInput"); if (!input) return;
    const val = input.value;
    if (val && val.trim()) { sendChat(val); input.value = ""; }
  });
  $("chatInput")?.addEventListener("keydown", (e) => {
    const input = $("chatInput"); if (!input) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const val = input.value;
      if (val && val.trim()) { sendChat(val); input.value = ""; }
    }
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      const prev = getChatLane();
      setChatLane("live");
      updateChatButtonLabel(getChatLane());
      const val = input.value;
      if (val && val.trim()) { sendChat(val); input.value = ""; }
      setChatLane(prev);
      updateChatButtonLabel(getChatLane());
    }
  });

  // “Ask Chip ▾” → Profile
  function toggleNavMenu(forceOpen) {
    const navMenu = $("navMenu"); if (!navMenu) return;
    if (typeof forceOpen === "boolean") { navMenu.hidden = !forceOpen; return; }
    navMenu.hidden = !navMenu.hidden;
  }
  $("navMenuBtn")?.addEventListener("click", (e) => { e.stopPropagation(); toggleNavMenu(); });
  document.addEventListener("click", (e) => {
    const navMenu = $("navMenu");
    if (!navMenu?.hidden && !navMenu.contains(e.target) && e.target !== $("navMenuBtn")) toggleNavMenu(false);
  });
  $("navProfile")?.addEventListener("click", async () => {
    toggleNavMenu(false);
    setProfileModalMode("edit");
    await loadProfileIntoForm();
    show($("profileModal"), "flex");
  });

  // Disconnect button
  $("btnDisconnect")?.addEventListener("click", () => {
    try {
      _chipStep("disconnect", "teardown");
      _vm_disarmVAD();
      _vm_stopPlayback();
      _chipClearIdleNudge();
      if (typeof respRelease === "function") { try { respRelease(); } catch {} }
      respRelease = null;
      _chipSetState("idle");
      _chipGuide("Disconnected. Press Start to begin a new session.");
      // If we have an existing stream from record.js, those tracks were stopped there
    } catch (e) { console.warn("disconnect error", e); }
  });

  // Wire login/profile handlers
  wireLoginAndProfileHandlers();

  // Boot
  (async () => {
    const g = await gate({ applyLayout: true });
    if (g && g.ok) {
      _chipGuide("Press Start or Chat to speak with Chip.");
      _chipStep("boot", "ready");
    }
  })();
});

// --- Session starters ---
async function startStaticSession() {
  try {
    _chipSetState("greeting");
    for (let i = 0; i < GREETING_FILES.length; i++) {
      const url = STATIC_AUDIO_BASE + GREETING_FILES[i];
      try {
        await tryPlayWithMouth(url);
        const chatPanel = $("chatPanel"); if (chatPanel) chatPanel.hidden = false;
        const chatInput = $("chatInput"); if (chatInput) { chatInput.placeholder = "Type your question…"; chatInput.focus(); }
        _chipStartWaitingCountdown();
        return;
      } catch (_) {}
    }
    throw new Error("No static audio found.");
  } catch (e) {
    console.warn(e?.message || e);
    alert((e && e.message) || "Couldn’t play the static greeting. Check your /static/chip/audio/ files.");
  }
}

async function startDynamicSession() {
  try {
    _chipSetState("greeting");
    _chipStep("POST /greet →", {});
    const { ok, data, status } = await j("/greet", { method: "POST", body: JSON.stringify({}) });

    if (!ok) {
      _chipStep("greet-failed", { status });
      // Fall back to static if server-side greet fails
      await startStaticSession();
      return;
    }

    // Expecting { audio?: string, reply?: string }
    const audioUrl = data && data.audio;
    const reply = data && data.reply;

    if (reply && typeof reply === "string") {
      appendMessage("chip", reply);
    }

    if (audioUrl) {
      try {
        await tryPlayWithMouth(audioUrl);
      } catch (e) {
        console.warn("Dynamic greet audio failed, continuing text-only greet.", e);
      }
    }

    const chatPanel = $("chatPanel"); if (chatPanel) chatPanel.hidden = false;
    const chatInput = $("chatInput"); if (chatInput) { chatInput.placeholder = "Ask me anything about Pure Storage…"; chatInput.focus(); }

    _chipStartWaitingCountdown();
    _chipSetState("idle");
    _chipStep("greet", "ready");
  } catch (e) {
    console.warn("startDynamicSession error:", e);
    // As a safety, try static
    try { await startStaticSession(); } catch {}
  }
}
