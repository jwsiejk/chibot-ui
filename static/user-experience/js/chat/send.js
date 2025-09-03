// chat/send.js — DIAGNOSTIC BUILD (WS-only, no gating), extra logging
import { j, wsConnect } from "../core/api.js";
import { _chipGuide, _chipSetState, _chipStep, _chipClearIdleNudge } from "../core/state.js";
import { appendMessage } from "./ui.js";
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
        if (msg.type === "end") { _muteStream = false; respRelease(); stopStream(); }
        return;
      }

      switch (msg.type) {
        case "ready":
          dbg("WS <- ready");
          break;
        case "partial_text":
          dbg("WS <- partial_text");
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
          respRelease();
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
  try { _muteStream = true; respRelease(); stopStream(); } catch(e){}
  try { if (_ws && _ws.isOpen && _ws.isOpen()) { _ws.send({ type: "cancel" }); dbg("WS -> cancel"); } } catch(e){}
});

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

export async function handleVoiceOnceResponse(ctx){
  const blob  = ctx && ctx.blob;
  const durMs = ctx && ctx.durMs || 0;
  if (!blob) { dbg("handleVoiceOnceResponse called with no blob"); return; }
  if (durMs < 300) { _chipGuide("I heard something very short. Try again?"); dbg("Short utterance ignored", durMs); return; }

  _chipStep("voice-once →", { durMs: durMs, size: blob.size });
  const fd = new FormData(); fd.append("audio", blob, "input.webm");
  if (!respAcquire()) { dbg("respAcquire failed"); return; }

  function onCancel(){ try { respRelease(); } catch(e){} }
  window.addEventListener("chip:tts-cancel", onCancel);

  try {
    _chipSetState("thinking");
    dbg("POST /api/v1/voice/stt", { size: blob.size, durMs: durMs });
    const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
    let data = {};
    try { data = await res.json(); } catch {}
    dbg("STT ->", { ok: res.ok, status: res.status, hasReply: !!(data && data.reply), hasAudio: !!(data && (data.audio || data.audio_base64 || data.audio_b64)) });

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
