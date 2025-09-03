// chat/send.js — DIAGNOSTIC BRIDGE v2 (WS-only typed, STT→WS guaranteed)
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
import { _vm_disarmVAD, _vm_armVAD } from "../voice/vad.js";

let _ws = null;
let _speaking = false; // when true, ignore barge-in and keep mic muted during playback
let _muteStream = false;
let _getLane = function(){ return "text"; };
let _wsReady = false;

export function wireChatLane(getLane, setLane){ if (typeof getLane === "function") _getLane = getLane; }

function dbg(){ try { window.acDebug && window.acDebug.log && window.acDebug.log.apply(null, arguments); } catch {} }

function _ensureWS(){
  if (_ws && _ws.isOpen && _ws.isOpen()) return;
  _wsReady = false;
  _ws = wsConnect("/ws/v1/chat", {
    onOpen: function(){ _chipStep("ws.chat: open", {}); dbg("WS open"); },
    onClose: function(){ _chipStep("ws.chat: close", {}); dbg("WS close"); _wsReady=false; },
    onError: function(e){ _chipStep("ws.chat: error", String(e && e.message || e)); dbg("WS error", e && (e.message || String(e))); },
    onMessage: function(msg){
      try { if (typeof msg === "string") msg = JSON.parse(msg); } catch(e){}
      if (!msg || typeof msg !== "object") return;

      if (msg.type === "ready"){ _wsReady = true; dbg("WS <- ready"); }

      if (_muteStream) {
        if (msg.type === "end") { _muteStream = false; respRelease(); stopStream(); }
        return;
      }

      switch (msg.type) {
        case "partial_text":
          dbg("WS <- partial_text");
          break;
        case "final_text":
          dbg("WS <- final_text");
          if (msg.text && String(msg.text).trim()) appendMessage("assistant", String(msg.text).trim());
          break;
        case "audio_chunk": {
          if (!_speaking) { _speaking = true; try { _vm_disarmVAD(); } catch(e){} }
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
          _speaking = false; try { _vm_armVAD(); } catch(e){}
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

function _wsSendWhenReady(payload, attempts=20){
  _ensureWS();
  const sendNow = () => {
    if (_ws && _ws.isOpen && _ws.isOpen()){
      try { _ws.send(payload); dbg("WS ->", payload.type, { len: (payload.text||"").length, lane: _getLane() }); } catch(e){ dbg("WS send error", e && (e.message || String(e))); }
      return true;
    }
    return false;
  };
  if (sendNow()) return;
  let tries = 0;
  (function loop(){
    if (sendNow()) return;
    if (++tries >= attempts) { dbg("WS send gave up (not open)"); return; }
    setTimeout(loop, 120);
  })();
}

// Barge‑in: cancel local playback and mute incoming stream
window.addEventListener("chip:bargein", function(){
  if (_speaking) { dbg("barge-in ignored while assistant speaking"); return; }
  try { _vm_stopPlayback(); } catch(e){}
  try { _muteStream = true; respRelease(); stopStream(); } catch(e){}
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

  const payload = { type: "user_text", text: text, meta: { lane: _getLane(), source: "typed" } };
  _wsSendWhenReady(payload);
  _chipSetState("responding");
}

/* ---------------------- One-shot voice turn (STT → WS) ------------------- */
export async function handleVoiceOnceResponse(ctx){
  const blob  = ctx && ctx.blob;
  const durMs = ctx && ctx.durMs || 0;
  if (!blob) { dbg("handleVoiceOnceResponse called with no blob"); return; }
  if (durMs < 350 || blob.size < 8000) { _chipGuide("Heard a very short utterance—try again?"); dbg("Short utterance ignored", { durMs, size: blob.size }); return; }

  _chipStep("voice-once →", { durMs: durMs, size: blob.size });
  const fd = new FormData(); fd.append("audio", blob, "input.webm");
  if (!respAcquire()) { dbg("respAcquire failed"); return; }

  function onCancel(){ try { respRelease(); } catch(e){} }
  window.addEventListener("chip:tts-cancel", onCancel);

  try {
    _chipSetState("thinking");
    dbg("POST /api/v1/voice/stt (multipart)", { durMs, size: blob.size });
    const res = await fetch("/api/v1/voice/stt", { method: "POST", body: fd, credentials: "include" });
    let data = {};
    try { data = await res.json(); } catch {}
    dbg("STT ->", { ok: res.ok, status: res.status, keys: Object.keys(data || {}) });

    const transcript = String((data && (data.text || data.transcript || data.reply || "")) || "").trim();
    if (!transcript) { dbg("No transcript in STT response"); _chipSetState("idle"); return; }

    // Show what we heard
    appendMessage("user", transcript, null);

    // Send to WS chat to get the actual reply + audio
    const payload = { type: "user_text", text: transcript, meta: { lane: "live", source: "stt" } };
    _wsSendWhenReady(payload);

    _chipSetState("responding");
  } catch(e){
    dbg("handleVoiceOnceResponse error", e && (e.message || String(e)));
    _chipGuide("I ran into an issue processing that—try again?");
  } finally {
    window.removeEventListener("chip:tts-cancel", onCancel);
    respRelease();
  }
}
