// chat/send.js — DIAGNOSTIC + Voice via STT -> WS chat (lane=live), WS-only typed chat
import { j, wsConnect } from "../core/api.js";
import { _chipGuide, _chipSetState, _chipStep, _chipClearIdleNudge } from "../core/state.js";
import { appendMessage } from "./ui.js";
import { gate } from "../auth/profile.js";

import {
  tryPlayWithMouth,
  _vm_stopPlayback,
  startStream,
  stopStream,
  pushPCM16Base64
} from "../voice/playback.js";

let _ws = null;
let _muteStream = false;
let _getLane = function(){ return "text"; };

export function wireChatLane(getLane, setLane){ if (typeof getLane === "function") _getLane = getLane; }

function dbg(){ try { window.acDebug && window.acDebug.log && window.acDebug.log.apply(null, arguments); } catch {} }

function _ensureWS(){
  if (_ws && _ws.isOpen && _ws.isOpen()) return;
  _ws = wsConnect("/ws/v1/chat", {
    onOpen: function(){ _chipStep("ws.chat: open", {}); dbg("WS open"); },
    onClose: function(){ _chipStep("ws.chat: close", {}); dbg("WS close"); },
    onError: function(e){ _chipStep("ws.chat: error", String(e && e.message || e)); dbg("WS error", e && (e.message || String(e))); },
    onMessage: function(msg){
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch(e){}
      if (!msg || typeof msg !== "object") return;

      if (_muteStream) {
        if (msg.type === "end") { _muteStream = false; stopStream(); }
        return;
      }

      switch (msg.type) {
        case "ready":
          dbg("WS <- ready");
          break;
        case "final_text":
          dbg("WS <- final_text");
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
          dbg("WS <- end");
          stopStream();
          break;
        case "error":
          dbg("WS <- error frame", msg && (msg.code || msg.message));
          break;
      }
    }
  });
}

// Barge‑in: cancel local playback and mute incoming stream
window.addEventListener("chip:bargein", function(){
  try { _vm_stopPlayback(); } catch(e){}
  try { _muteStream = true; stopStream(); } catch(e){}
  try { if (_ws && _ws.isOpen && _ws.isOpen()) { _ws.send({ type: "cancel" }); dbg("WS -> cancel"); } } catch(e){}
});

/* -------------------------- Typed chat (WS only) -------------------------- */
export async function sendChat(message){
  const text = String(message||"").trim();
  if (!text) return;

  const okGate = await gate();
  if (!okGate.ok) { dbg("Gate failed on sendChat"); return; }

  _chipClearIdleNudge();
  appendMessage("user", text, null);
  _chipSetState("thinking");

  try {
    _ensureWS();
    if (!_ws || !_ws.isOpen || !_ws.isOpen()) {
      _chipSetState("followup");
      _chipGuide("Chat stream isn’t available. Please try again.");
      dbg("WS not open on sendChat");
      return;
    }
    const payload = { type: "user_text", text: text, meta: { lane: _getLane() } };
    try { _ws.send(payload); dbg("WS -> user_text", { len: text.length, lane: _getLane() }); } catch(e){ dbg("WS send error", e && (e.message || String(e))); }
    _chipSetState("responding");
  } catch(e){
    _chipSetState("followup");
    _chipGuide("Chat stream isn’t available. Please try again.");
    dbg("sendChat error", e && (e.message || String(e)));
  }
}

/* ---------------------- One-shot voice turn (STT -> WS) ------------------- */
export async function handleVoiceOnceResponse(ctx){
  const blob  = ctx && ctx.blob;
  const durMs = ctx && ctx.durMs || 0;
  if (!blob) { dbg("handleVoiceOnceResponse called with no blob"); return; }
  if (durMs < 350 || blob.size < 8000) { _chipGuide("Heard a very short utterance—try again?"); dbg("Short utterance ignored", { durMs, size: blob.size }); return; }

  _chipStep("voice-once →", { durMs: durMs, size: blob.size });

  // 1) STT
  const fd = new FormData(); fd.append("audio", blob, "input.webm");
  try {
    _chipSetState("thinking");
    dbg("POST /api/v1/voice/stt (multipart)", { size: blob.size, durMs: durMs });
    const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
    let data = {};
    try { data = await res.json(); } catch {}
    const transcript = String((data && (data.text || data.transcript || data.reply || "")) || "").trim();
    dbg("STT ->", { ok: res.ok, status: res.status, chars: transcript.length });

    if (!res.ok || !transcript) {
      _chipSetState("followup");
      _chipGuide("I didn’t catch that—mind repeating?");
      return;
    }

    // 2) Send transcript to chat over WS (lane=live)
    _ensureWS();
    if (!_ws || !_ws.isOpen || !_ws.isOpen()) {
      _chipSetState("followup");
      _chipGuide("Chat stream isn’t available. Please try again.");
      dbg("WS not open after STT");
      return;
    }

    appendMessage("user", transcript, null);
    const payload = { type: "user_text", text: transcript, meta: { lane: "live", source: "stt" } };
    _ws.send(payload);
    dbg("WS -> user_text (from STT)", { len: transcript.length });

    _chipSetState("responding");
    // audio will stream back via WS handlers
  } catch(e){
    dbg("handleVoiceOnceResponse error", e && (e.message || String(e)));
    _chipSetState("followup");
    _chipGuide("I ran into an issue processing that—try again?");
  }
}
