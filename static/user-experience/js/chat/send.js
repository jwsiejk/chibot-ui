// chat/send.js — WS-first, adds gate import and clean barge-in
// Core & UI
import { j, wsConnect } from "../core/api.js";
import { _chipGuide, _chipSetState, _chipStartWaitingCountdown, _chipStep, _chipClearIdleNudge } from "../core/state.js";
import { appendMessage, appendActions, _chipRenderSuggestions } from "./ui.js";
import { gate } from "../auth/profile.js";

// Voice
import {
  tryPlayWithMouth,
  _vm_stopPlayback,
  driveVisemes,
  respAcquire,
  respRelease,
  startStream,
  stopStream,
  pushPCM,
  setExpectedChannels,
  setStreamSampleRate
} from "../voice/playback.js";

// -------------------------- Streaming WebSocket --------------------------
let _ws = null;
let _streamPrimed = false;
let _muteStream = false;

function _ensureWS(){
  if (_ws && _ws.isOpen()) return;
  _ws = wsConnect("/ws/v1/chat", {
    onOpen(){ _chipStep("ws.chat: open", {}); },
    onClose(){ _chipStep("ws.chat: close", {}); _streamPrimed=false; },
    onError(e){ _chipStep("ws.chat: error", String(e && e.message || e)); },
    onMessage(msg){
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch(e){}
      if (!msg || typeof msg !== "object") return;
      if (_muteStream) {
        if (msg.type === "end") { _muteStream = false; respRelease(); stopStream(); }
        return;
      }
      switch (msg.type) {
        case "ready":
          _streamPrimed = true;
          break;
        case "partial_text":
          // Show incremental text if you support it
          break;
        case "final_text":
          if (msg.text && String(msg.text).trim()) appendMessage("assistant", String(msg.text).trim());
          break;
        case "audio_chunk":
          if (msg.pcm16 && Array.isArray(msg.pcm16)) {
            if (!startStream()) startStream();
            pushPCM(msg.pcm16);
          } else if (msg.audio && typeof msg.audio === "string") {
            tryPlayWithMouth(msg.audio);
          }
          break;
        case "end":
          respRelease();
          stopStream();
          break;
      }
    }
  });
}

// Bar­ge‑in: cancel local playback and mute incoming stream for this turn
window.addEventListener("chip:bargein", function(){
  try { _vm_stopPlayback(); } catch(e){}
  try { _muteStream = true; respRelease(); stopStream(); } catch(e){}
  try { if (_ws && _ws.isOpen()) { _ws.send({ type: "cancel" }); } } catch(e){}
});

// -------------------------- Helpers --------------------------
let _fu_lastOfferedAt = 0;
let _fu_turnsSinceOffer = 999;
function _offerFollowupOnce() {
  _fu_lastOfferedAt = Date.now();
  _fu_turnsSinceOffer = 0;
  const prompts = [
    "Want me to dig a bit deeper?",
    "Need a quick example?",
    "Should I pull the numbers behind that?",
    "I can check related items if you want."
  ];
  _chipRenderSuggestions([prompts[Math.floor(Math.random()*prompts.length)], "End chat"], function(s) {
    if (/end chat/i.test(s)) { _chipEndConversation(); return; }
    sendChat(s);
  });
}
function _classifyInput(text){
  const t = (text||"").trim();
  if (!t) return "empty";
  if (t.length <= 2) return "too_short";
  return "ok";
}
function _isEndTrigger(msg){ return /^(end|quit|bye)\b/i.test(String(msg||"").trim()); }
async function _handleCanned(cls){
  if (cls === "empty") { _chipGuide("Try asking a question like: “How do I size FlashArray for a VCF workload?”"); return; }
  if (cls === "too_short") { _chipGuide("Give me a bit more to work with?"); return; }
}

// -------------------------- Public API --------------------------
export async function sendChat(message){
  message = String(message||"");
  if (!message.trim()) return;

  if (_isEndTrigger(message)) { _chipEndConversation(); return; }

  const okGate = await gate();
  if (!okGate.ok) return;

  _chipClearIdleNudge();
  _fu_turnsSinceOffer = Math.min(_fu_turnsSinceOffer + 1, 99);

  const cls = _classifyInput(message);
  if (cls !== "ok") {
    appendMessage("user", message.trim(), null);
    await _handleCanned(cls);
    return;
  }

  appendMessage("user", message.trim(), null);
  _chipSetState("thinking");

  // --- WS streaming only (no REST fallback) ---
  try {
    _ensureWS();
    if (!_ws || !_ws.isOpen()) {
      _chipSetState("followup");
      _chipGuide("Chat stream isn’t available. Please try again.");
      return;
    }
    const payload = {
      type: "user_message",
      text: message,
      meta: { lane: "text" }
    };
    _ws.send(payload);
    _chipSetState("responding");
  } catch(e){
    _chipSetState("followup");
    _chipGuide("Chat stream isn’t available. Please try again.");
  } finally {
    if (_fu_turnsSinceOffer >= 2 && Date.now() - _fu_lastOfferedAt > 20000) {
      _offerFollowupOnce();
    }
  }
}

// One-shot voice pipeline (when user speaks and VAD stops)
export async function handleVoiceOnceResponse({ blob, durMs }){
  if (!blob) return;
  _chipStep("voice-once →", { durMs, size: blob.size });
  const fd = new FormData(); fd.append("audio", blob, "input.webm");
  if (!respAcquire()) { return; }

  const onCancel = function(){ try { respRelease(); } catch(e){} };
  window.addEventListener("chip:tts-cancel", onCancel);

  try {
    _chipSetState("thinking");
    const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
    const data = await res.json().catch(function(){ return {}; });

    // Expect server to reply with final text/audio; if not present, stop here (no fallback in this mode)
    if (data && data.reply) appendMessage("assistant", String(data.reply).trim());
    if (data && data.audio) {
      try { await tryPlayWithMouth(data.audio); } catch(e){}
    } else if (data && (data.audio_base64 || data.audio_b64)) {
      try {
        const a = new Audio("data:audio/mpeg;base64," + (data.audio_base64 || data.audio_b64));
        await a.play();
      } catch(e){}
    }

    _chipSetState("followup");
  } finally {
    window.removeEventListener("chip:tts-cancel", onCancel);
    respRelease();
  }
}

// expose to main.js
export function setArmVAD(fn){ if (typeof fn === "function") window.__AC_setArmVAD = fn; }
export function _chipEndConversation(){ window.dispatchEvent(new Event("chip:tts-cancel")); _chipGuide("Okay, ending the chat here. Press Start when you’re ready again."); }
