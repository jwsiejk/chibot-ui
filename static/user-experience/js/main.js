// main.js — dynamic-only boot, Start button, mic/VAD wiring (ES module)

import { $, show, hide, setToolbarHeightVar } from "./core/dom.js";
import {
  _chipGuide,
  _chipSetState,
  _chipStartWaitingCountdown,
  _chipStep,
  setRenderSuggestions,
  setArmVADHook,
  _chipClearIdleNudge
} from "./core/state.js";
import { j } from "./core/api.js";
import {
  setProfileModalMode,
  loadProfileIntoForm,
  gate,
  wireLoginAndProfileHandlers
} from "./auth/profile.js";
import {
  appendMessage,
  appendActions,
  _chipRenderSuggestions,
  updateChatButtonLabel,
  wireChatMenu
} from "./chat/ui.js";
import {
  sendChat,
  handleVoiceOnceResponse,
  wireChatLane,
  setArmVAD as setArmVADForSend,
  _chipEndConversation
} from "./chat/send.js";
import { tryPlayWithMouth, _vm_stopPlayback } from "./voice/playback.js";
import {
  _vm_armVAD,
  _vm_disarmVAD,
  setMicUIUpdater,
  setGuide as setVoiceGuide,
  setRecordCallbacks
} from "./voice/vad.js";
import { _vm_stopRecording, setStream as setRecordStream } from "./voice/record.js";

// Persisted chat lane (text vs live)  <-- must be before any use
// Persisted chat lane (text vs live)  <-- must be before any use
let chatLane = (localStorage.getItem("chatLane") === "text") ? "text" : "live";
const getChatLane = () => chatLane;
const setChatLane = (lane) => {
  chatLane = (lane === "text") ? "text" : "live";
  try { localStorage.setItem("chatLane", chatLane); } catch (e) {}
};

// --- Chip image hardening & mouth normalization (paths reflect your repo) ---
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

function rehydrateChip() {
  const chip  = document.getElementById("chipImage");
  const mouth = document.getElementById("chipMouthImg");
  if (!chip) return;

  // keep Chip visible
  chip.classList.remove("hidden");
  chip.style.display    = "block";
  chip.style.visibility = "visible";
  chip.style.opacity    = "1";

  // fix bad src
  chip.onerror = () => {
    chip.src = `${CHIP_SRC}?v=${Date.now()}`;
    chip.classList.remove("hidden");
  };

  if (mouth) {
    mouth.onerror = () => setMouth("mouth_neutral.png");
  }
}

// Build fingerprint for cache verification
console.log("UI build ⏱ 2025-08-14-03");

// ===== single DOMContentLoaded =====
document.addEventListener("DOMContentLoaded", () => {
  // Chip setup
  rehydrateChip();
  requestAnimationFrame(rehydrateChip);
  setTimeout(rehydrateChip, 250);

  // Toolbar Chat controls
  const btnChat      = $("btnChat");
  const btnChatText  = $("btnChatText");
  const btnChatLive  = $("btnChatLive");

  if (btnChatText) {
    btnChatText.addEventListener("click", () => { setChatLane("text"); ac_openOnlyChat("text"); });
  }
  if (btnChatLive) {
    btnChatLive.addEventListener("click", () => { setChatLane("live"); ac_openOnlyChat("live"); });
  }
  if (btnChat) {
    btnChat.addEventListener("click", () => { ac_toggleChat(getChatLane()); });
  }
});

  // ========= Ask Chip (UI helpers added) =========
  // Admin call log (writes only if #adminLog exists)
  const ac_logAdminin = (event, detail = "") => {
    const wrap = $("adminLog");
    if (!wrap) return;
    const row = document.createElement("div");
    row.className = "admin-log-row";
    const ts = new Date().toLocaleTimeString();
    row.textContent = `[${ts}] ${event}${detail ? " — " + detail : ""}`;
    wrap.insertBefore(row, wrap.firstChild || null);
  };

  // Status banner
  const ac_setTopStatus = (text) => {
    const el = $("statusBanner");
    if (el) el.textContent = text || "";
  };

  // Exclusive chat windows (Text vs Live/TTS)
  const ac_chatTextEl = $("chatText");
  const ac_chatTTSEl  = $("chatTTS");

  const ac_show = (el) => { if (el) el.classList.remove("hidden"); };
  const ac_hide = (el) => { if (el) el.classList.add("hidden"); };
  const ac_visible = (el) => !!(el && !el.classList.contains("hidden"));

  const ac_openOnlyChat = (which /* 'text' | 'live' */) => {
    if (which === "text") {
      ac_show(ac_chatTextEl); ac_hide(ac_chatTTSEl);
      ac_logAdminin("ui", "opened text, closed tts");
      try { ac_chatTextEl?.querySelector("input,textarea")?.focus(); } catch {}
    } else { // 'live'
      ac_show(ac_chatTTSEl); ac_hide(ac_chatTextEl);
      ac_logAdminin("ui", "opened tts, closed text");
    }
  };

  const ac_toggleChat = (which /* 'text' | 'live' */) => {
    if (which === "text") {
      if (ac_visible(ac_chatTextEl)) { ac_hide(ac_chatTextEl); ac_logAdminin("ui", "closed text"); }
      else ac_openOnlyChat("text");
    } else {
      if (ac_visible(ac_chatTTSEl)) { ac_hide(ac_chatTTSEl); ac_logAdminin("ui", "closed tts"); }
      else ac_openOnlyChat("live");
    }
  };

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

  // Keep existing menu wiring, but when lane changes we also reflect the panel state.
  wireChatMenu(getChatLane, (lane) => {
    setChatLane(lane);
    updateChatButtonLabel(getChatLane());
    // Show the chosen lane and hide the other
    ac_openOnlyChat(getChatLane() === "text" ? "text" : "live");
  }, () => { /* existing no-op retained */ });

  // Additionally, if your HTML menu has explicit lane items, wire them too (harmless if missing)
  const chatMenu = $("chatMenu");
  if (chatMenu) {
    const textItem = chatMenu.querySelector('[data-lane="text"]');
    const liveItem = chatMenu.querySelector('[data-lane="live"]');
    textItem && textItem.addEventListener("click", () => {
      // click-again-to-close behavior
      if (ac_visible(ac_chatTextEl)) ac_toggleChat("text");
      else { setChatLane("text"); updateChatButtonLabel(getChatLane()); ac_openOnlyChat("text"); }
    });
    liveItem && liveItem.addEventListener("click", () => {
      if (ac_visible(ac_chatTTSEl)) ac_toggleChat("live");
      else { setChatLane("live"); updateChatButtonLabel(getChatLane()); ac_openOnlyChat("live"); }
    });
  }

  // Mic UI / VAD visuals
  setMicUIUpdater((on, recording = false) => {
    const btnMic = $("btnMic");
    if (!btnMic) return;
    btnMic.classList.toggle("armed", !!on);
    btnMic.classList.toggle("recording", !!recording);
    if (recording)      btnMic.textContent = "🎙️ Recording (tap to stop)";
    else if (on)        btnMic.textContent = "🎤 Listening";
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
    ac_setTopStatus("Connecting");
    ac_logAdminin("session", "start clicked");
    _chipGuide("Starting");
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
      ac_setTopStatus("Disconnected. Press Start to begin a new session.");
      ac_logAdminin("session", "disconnected");
      _chipGuide("Disconnected. Press Start to begin a new session.");
    } catch (e) { console.warn("disconnect error", e); }
  });

  // Auth wiring
  wireLoginAndProfileHandlers();

  // Boot hint
  (async () => {
    const g = await gate({ applyLayout: true });
    if (g && g.ok) {
      ac_setTopStatus("Disconnected. Press Start to begin a new session.");
      _chipGuide("Press Start to speak with Chip.");
      _chipStep("boot", "ready");
    }
  })();

  // Surface unexpected errors in admin log to help with “something went sideways”
  window.addEventListener("error", (e) => ac_logAdminin("error", e.message || "unknown"));
  window.addEventListener("unhandledrejection", (e) => ac_logAdminin("promise", (e?.reason && e.reason.message) || String(e?.reason || "unknown")));
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
    const chatInput = $("chatInput"); if (chatInput) { chatInput.placeholder = "Ask me anything about Pure Storage"; chatInput.focus(); }

    _chipStartWaitingCountdown();
    _chipSetState("idle");
    _chipStep("greet", "ready");
  } catch (e) {
    console.warn("startDynamicSession error:", e);
    _chipSetState("idle");
  }
}
