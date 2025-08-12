// main.js — dynamic-only boot, Start button, mic/VAD wiring (ES module)

import { $, show, hide, setToolbarHeightVar } from "./core/dom.js";
import { _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep, setRenderSuggestions, setArmVADHook, _chipClearIdleNudge } from "./core/state.js";
import { j } from "./core/api.js";
import { setProfileModalMode, loadProfileIntoForm, gate, wireLoginAndProfileHandlers } from "./auth/profile.js";
import { appendMessage, appendActions, _chipRenderSuggestions, updateChatButtonLabel, wireChatMenu } from "./chat/ui.js";
import { sendChat, handleVoiceOnceResponse, wireChatLane, setArmVAD as setArmVADForSend, _chipEndConversation } from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import { _vm_armVAD, _vm_disarmVAD, setMicUIUpdater, setGuide as setVoiceGuide, setRecordCallbacks } from "./voice/vad.js";
import { _vm_stopRecording, setStream as setRecordStream } from "./voice/record.js";

// Persisted chat lane (text vs live)
let chatLane = (localStorage.getItem("chatLane") === "text") ? "text" : "live";
const getChatLane = () => chatLane;
const setChatLane = (lane) => { chatLane = (lane === "text") ? "text" : "live"; try { localStorage.setItem("chatLane", chatLane); } catch {} };

window.addEventListener("load", setToolbarHeightVar);
window.addEventListener("resize", setToolbarHeightVar);
window.addEventListener("orientationchange", setToolbarHeightVar);

document.addEventListener("DOMContentLoaded", () => {
  // --- session gate: disable Start while a session is active ---
  let sessionActive = false;
  function setSessionActive(on) {
    sessionActive = !!on;
    const b = $("btnStart");
    if (b) {
      b.disabled = sessionActive;
      b.classList.toggle("disabled", sessionActive);
      b.setAttribute("aria-disabled", String(sessionActive));
    }
  }

  // Suggestion renderer (idle nudges etc.)
  setRenderSuggestions((sugs) =>
    _chipRenderSuggestions(sugs, (s) => {
      if (/end chat/i.test(s)) { _chipEndConversation(); return; }
      sendChat(s);
    })
  );

  // Chat lane + UI
  wireChatLane(getChatLane, setChatLane);
  setArmVADForSend(() => _vm_armVAD());
  updateChatButtonLabel(getChatLane());
  wireChatMenu(getChatLane, setChatLane, () => { /* no-op */ });

  // Mic UI / VAD visuals
  setMicUIUpdater((on, recording = false) => {
    const btnMic = $("btnMic");
    if (!btnMic) return;
    btnMic.classList.toggle("armed", !!on);
    btnMic.classList.toggle("recording", !!recording);
    if (recording)      btnMic.textContent = "🎙️ Recording… (tap to stop)";
    else if (on)        btnMic.textContent = "🎤 Listening…";
    else                btnMic.textContent = "🎤 Mic";
  });

  setVoiceGuide((text) => _chipGuide(text));
  setRecordCallbacks(
    async () => { /* onStartRecording */ },
    async () => {
      // onStopRecording → deliver clip to /api/voice-once
      await _vm_stopRecording(async (blob, durMs) => {
        await handleVoiceOnceResponse({ blob, durMs });
      });
    }
  );

  // Arming VAD should barge-in any playback first
  setArmVADHook(async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      setRecordStream(s);
    } catch (err) {
      console.warn("getUserMedia failed:", err);
      _chipGuide("I can’t access your mic. Check browser permissions and try again.");
      return;
    }
    _vm_stopPlayback(); // barge-in: flush streaming & <audio>
    await _vm_armVAD();
  });

  // Start = dynamic greet (guard against double-starts, then auto-arm VAD)
  $("btnStart")?.addEventListener("click", async () => {
    if (sessionActive) return; // hard gate
    const okGate = await gate({ applyLayout: true }); if (!okGate.ok) return;

    setSessionActive(true);
    _chipGuide("Starting…");
    await startDynamicSession(); // awaits greet audio

    // Auto-arm VAD after greet (only if not already armed)
    const alreadyArmed = $("btnMic")?.classList.contains("armed");
    _chipGuide("Now listening — start talking after the tone.");
    if (!alreadyArmed) {
      _chipStep("vad", "arming-post-greet");
      try { await _vm_armVAD(); } catch (e) { console.warn("VAD arm failed", e); }
    }

    // If user says nothing for a while, nudge
    setTimeout(() => {
      const stillArmed = $("btnMic")?.classList.contains("armed");
      if (stillArmed) _chipGuide("I didn’t catch anything—check your mic or click Mic to try again.");
    }, 8000);
  });

  // Mic button toggle (simple VAD arm/disarm)
  $("btnMic")?.addEventListener("click", async () => {
    const btnMic = $("btnMic");
    if (btnMic?.classList.contains("recording")) return; // recording stops via record.js
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

    // Ctrl+Enter → send as "live" then restore lane
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

  // “Ask Chip ▾” menu + items (Profile, History, Logout)
  function toggleNavMenu(forceOpen) {
    const navMenu = $("navMenu"); if (!navMenu) return;
    if (typeof forceOpen === "boolean") { navMenu.hidden = !forceOpen; return; }
    navMenu.hidden = !navMenu.hidden;
  }
  $("navMenuBtn")?.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    toggleNavMenu();
  });
  document.addEventListener("click", (e) => {
    const navMenu = $("navMenu");
    if (!navMenu?.hidden && !navMenu.contains(e.target) && e.target !== $("navMenuBtn")) {
      toggleNavMenu(false);
    }
  });

  $("navProfile")?.addEventListener("click", async () => {
    toggleNavMenu(false);
    setProfileModalMode("edit");
    await loadProfileIntoForm();
    show($("profileModal"), "flex");
  });

  $("navHistory")?.addEventListener("click", async () => {
    toggleNavMenu(false);
    const { ok, data } = await j("/history", { method: "POST", body: "{}" });
    if (ok && data?.response) appendMessage("chip", data.response);
  });

  $("navLogout")?.addEventListener("click", async () => {
    toggleNavMenu(false);
    try { await fetch("/logout", { method: "POST" }); } catch {}
    location.reload();
  });

  // End session (reenables Start)
  $("btnDisconnect")?.addEventListener("click", () => {
    try {
      _chipStep("disconnect", "teardown");
      _vm_disarmVAD();
      _vm_stopPlayback();
      _chipClearIdleNudge();
      _chipSetState("idle");
      setSessionActive(false);
      _chipGuide("Disconnected. Press Start to begin a new session.");
    } catch (e) { console.warn("disconnect error", e); }
  });

  // Auth wiring
  wireLoginAndProfileHandlers();

  // Boot hint
  (async () => {
    const g = await gate({ applyLayout: true });
    if (g && g.ok) {
      _chipGuide("Press Start to speak with Chip.");
      _chipStep("boot", "ready");
    }
  })();
});


// --- Dynamic session (no static fallback) ---
async function startDynamicSession() {
  try {
    _chipSetState("greeting");
    _chipStep("POST /greet →", {});
    const { ok, data, status } = await j("/greet", { method: "POST", body: JSON.stringify({}) });

    if (!ok) {
      _chipStep("greet-failed", { status });
      _chipSetState("idle");
      _chipGuide("Couldn’t start the greeting. Try again?");
      return;
    }

    // Expecting { audio?: string, reply?: string }
    const audioUrl = data && data.audio;
    const reply = data && data.reply;

    if (reply && typeof reply === "string") {
      appendMessage("chip", reply);
    }

    if (audioUrl) {
      try { await tryPlayWithMouth(audioUrl); } catch (e) { console.warn("Greet audio failed", e); }
    }

    const chatPanel = $("chatPanel"); if (chatPanel) chatPanel.hidden = false;
    const chatInput = $("chatInput"); if (chatInput) { chatInput.placeholder = "Ask me anything about Pure Storage…"; chatInput.focus(); }

    _chipStartWaitingCountdown();
    _chipSetState("idle");
    _chipStep("greet", "ready");
  } catch (e) {
    console.warn("startDynamicSession error:", e);
    _chipSetState("idle");
  }
}
