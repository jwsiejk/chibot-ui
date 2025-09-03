// chat/send.js — WS-only, no keyword gating, correct payload, lower voice min duration to 300ms, exports wireChatLane
import { j, wsConnect } from "../core/api.js";
import { _chipGuide, _chipSetState, _chipStep, _chipClearIdleNudge } from "../core/state.js";
import { appendMessage, _chipRenderSuggestions } from "./ui.js";
import { gate } from "../auth/profile.js";

import {
  tryPlayWithMouth,
  _vm_stopPlayback,
  respAcquire,
  respRelease,
  startStream,
  stopStream,
  pushPCM16Base64
} from "../voice/playback.js";

let _ws = null;
let _muteStream = false;
let _getLane = function(){ return "text"; };

export function wireChatLane(getLane, setLane){ if (typeof getLane === "function") _getLane = getLane; }

function _ensureWS(){
  if (_ws && _ws.isOpen && _ws.isOpen()) return;
  _ws = wsConnect("/ws/v1/chat", {
    onOpen: function(){ _chipStep("ws.chat: open", {}); },
    onClose: function(){ _chipStep("ws.chat: close", {}); },
    onError: function(e){ _chipStep("ws.chat: error", String(e && e.message || e)); },
    onMessage: function(msg){
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch(e){}
      if (!msg || typeof msg !== "object") return;

      if (_muteStream) {
        if (msg.type === "end") { _muteStream = false; respRelease(); stopStream(); }
        return;
      }

      switch (msg.type) {
        case "ready":
          break;
        case "partial_text":
          // optional: could render streaming text
          break;
        case "final_text":
          if (msg.text && String(msg.text).trim()) appendMessage("assistant", String(msg.text).trim());
          break;
        case "audio_chunk": {
          const b16 = msg.b16 || msg.pcm16_b64 || msg.pcm16_base64 || msg.b64;
          if (typeof b16 === "string" && b16) {
            try { startStream({ sampleRate: msg.sr || 24000 }); } catch(e){}
            try { pushPCM16Base64(b16); } catch(e){}
          } else if (msg.audio && typeof msg.audio === "string") {
            tryPlayWithMouth(msg.audio);
          }
          break;
        }
        case "end":
          respRelease();
          stopStream();
          break;
      }
    }
  });
}

// Voice barge‑in: cancel local playback and mute incoming stream for this turn
window.addEventListener("chip:bargein", function(){
  try { _vm_stopPlayback(); } catch(e){}
  try { _muteStream = true; respRelease(); stopStream(); } catch(e){}
  try { if (_ws && _ws.isOpen && _ws.isOpen()) { _ws.send({ type: "cancel" }); } } catch(e){}
});

/* -------------------------- Typed chat (WS only) -------------------------- */
export async function sendChat(message){
  const text = String(message||"").trim();
  if (!text) return;

  if ((await gate()).ok !== true) return;

  _chipClearIdleNudge();
  appendMessage("user", text, null);
  _chipSetState("thinking");

  try {
    _ensureWS();
    if (!_ws || !_ws.isOpen || !_ws.isOpen()) {
      _chipSetState("followup");
      _chipGuide("Chat stream isn’t available. Please try again.");
      return;
    }
    const payload = { type: "user_text", text: text, meta: { lane: _getLane() } };
    _ws.send(payload);
    _chipSetState("responding");
  } catch(e){
    _chipSetState("followup");
    _chipGuide("Chat stream isn’t available. Please try again.");
  }
}

/* ---------------------- One-shot voice turn (STT path) ------------------- */
export async function handleVoiceOnceResponse(ctx){
  const blob  = ctx && ctx.blob;
  const durMs = ctx && ctx.durMs || 0;
  if (!blob) return;
  if (durMs < 300) { _chipGuide("I heard something very short. Try again?"); return; } // lowered from 600ms

  _chipStep("voice-once →", { durMs: durMs, size: blob.size });
  const fd = new FormData(); fd.append("audio", blob, "input.webm");
  if (!respAcquire()) { return; }

  function onCancel(){ try { respRelease(); } catch(e){} }
  window.addEventListener("chip:tts-cancel", onCancel);

  try {
    _chipSetState("thinking");
    const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
    const data = await res.json().catch(function(){ return {}; });

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
