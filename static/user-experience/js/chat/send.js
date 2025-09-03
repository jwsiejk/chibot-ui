
/**
 * send.js — MAX COMPAT (no-op legacy stubs) + soft/echo‑aware barge‑in
 *
 * This module provides the new runtime API AND defines legacy names as NO‑OP stubs
 * so older imports do not crash the app. The stubs DO NOT send messages or change
 * runtime behavior in Ask Chip. Remove them after migrating callers.
 *
 * New API (use these):
 *   - start()
 *   - attachSocket(ws)
 *   - handleVoiceOnceResponse(evtOrMsg)
 *   - sendUserText(text, ctx?)
 *   - interrupt(reason?)
 *   - setTTSPlayer(player)
 *
 * Legacy names (temporary NO‑OPS; safe to delete later):
 *   - sendChat, sendText, sendTextAndContext, sendMessage
 *   - attachWS, setWS, initChat, init, stop
 *   - handleOnceResponse, handleWsMessage, handleVoiceResponseOnce
 *   - wireChatLane
 */

import { SoftBargeIn } from "./soft-bargein.js";
import * as VAD from "../voice/vad.js";

// --------------------------- Utilities ---------------------------

function emitState(state) {
  try { window.dispatchEvent(new CustomEvent("chip:state", { detail: { state } })); } catch {}
}

function base64ToArrayBuffer(b64) {
  const binary_string = atob(b64);
  const len = binary_string.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = binary_string.charCodeAt(i);
  return bytes.buffer;
}

// Lightweight fallback player if window.ttsPlayer is not present.
function createFallbackTTSPlayer(mime = "audio/webm") {
  let chunks = [];
  let audio = new Audio();
  audio.preload = "auto";
  let muted = false;

  return {
    appendChunk(data) {
      const buf = data instanceof ArrayBuffer ? data : (data?.buffer || new ArrayBuffer(0));
      chunks.push(new Blob([buf], { type: mime }));
    },
    finalize() {
      if (!chunks.length) return;
      try { URL.revokeObjectURL(audio.src); } catch {}
      const blob = new Blob(chunks, { type: mime });
      chunks = [];
      const url = URL.createObjectURL(blob);
      audio.src = url;
      audio.muted = muted;
      audio.play().catch(() => {});
    },
    stop() {
      try { audio.pause(); audio.currentTime = 0; } catch {}
    },
    mute(m) {
      muted = !!m;
      try { audio.muted = muted; } catch {}
    }
  };
}

// --------------------------- Module state ---------------------------

let ttsPlayer = null;
let barge = null;
let wsRef = null;
let started = false;

// --------------------------- New API ---------------------------

export async function start() {
  if (started) return;
  started = true;

  // Prepare audio player
  ttsPlayer = window.ttsPlayer || createFallbackTTSPlayer();

  // Arm VAD (request mic permissions)
  try { await VAD.arm(); } catch (e) { console.warn("VAD arm failed:", e); }

  // Instantiate soft barge-in controller; socket attached via attachSocket()
  barge = new SoftBargeIn({
    vad: VAD,
    socket: wsRef,
    ttsPlayer,
    confirmMs: (window.CHIP_BARGE_CONFIRM_MS || 420),
    echoThresholdBoost: (window.CHIP_ECHO_THRESHOLD_BOOST || 1.9),
    onPendingUI: (isPending) => {
      document.body.classList.toggle("chip-paused-pending", !!isPending);
    },
    interruptCmd: "interrupt"
  });
  barge.wire();

  emitState("ready");

  // Keyboard override
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") interrupt("keyboard");
  });
}

export function setTTSPlayer(player) {
  ttsPlayer = player || ttsPlayer;
}

export function attachSocket(ws) {
  wsRef = ws || wsRef;
  if (barge) barge.socket = wsRef;
}

export function sendUserText(text, ctx = {}) {
  if (!wsRef || wsRef.readyState !== 1) return;
  const payload = { type: "user", mode: "text", text, ctx };
  try { wsRef.send(JSON.stringify(payload)); } catch {}
}

export function interrupt(reason = "manual") {
  try { barge?.immediateInterrupt?.(reason); } catch {}
}

/**
 * Main message handler to be used by external WS wiring.
 * Accepts either a native MessageEvent (evt.data) or a plain object msg.
 */
export async function handleVoiceOnceResponse(evtOrMsg) {
  let msg = null;
  let maybeWS = null;

  if (evtOrMsg && 'data' in evtOrMsg) {
    // Native MessageEvent
    maybeWS = evtOrMsg.currentTarget || evtOrMsg.target || null;
    if (typeof evtOrMsg.data === "string") {
      try { msg = JSON.parse(evtOrMsg.data); } catch { msg = null; }
    } else {
      // Binary payload (Blob/ArrayBuffer): treat as audio chunk
      let buf;
      if (evtOrMsg.data instanceof ArrayBuffer) {
        buf = evtOrMsg.data;
      } else if (evtOrMsg.data instanceof Blob) {
        buf = await evtOrMsg.data.arrayBuffer();
      }
      if (buf) {
        barge?.onAssistantAudioStart?.();
        (ttsPlayer || (ttsPlayer = createFallbackTTSPlayer())).appendChunk(buf);
        return;
      }
    }
  } else if (evtOrMsg && typeof evtOrMsg === "object") {
    msg = evtOrMsg;
  }

  if (maybeWS && !wsRef) attachSocket(maybeWS);
  if (!msg) return;

  switch (msg.type) {
    case "state":
      emitState(msg.state);
      break;
    case "text":
      try { window.dispatchEvent(new CustomEvent("chip:text", { detail: msg })); } catch {}
      break;
    case "audio_chunk": {
      barge?.onAssistantAudioStart?.();
      let buf;
      if (typeof msg.data === "string")       buf = base64ToArrayBuffer(msg.data);
      else if (msg.data?.type === "Buffer" && Array.isArray(msg.data.data))
                                              buf = new Uint8Array(msg.data.data).buffer;
      else if (Array.isArray(msg.data))       buf = new Uint8Array(msg.data).buffer;
      else                                    break;
      (ttsPlayer || (ttsPlayer = createFallbackTTSPlayer())).appendChunk(buf);
      break;
    }
    case "end":
      barge?.onAssistantAudioEnd?.();
      (ttsPlayer || (ttsPlayer = createFallbackTTSPlayer())).finalize();
      emitState("ready");
      break;
    case "error":
      console.error("WS error msg:", msg);
      break;
    default:
      // no-op
      break;
  }
}

// --------------------------- Legacy NO-OP stubs ---------------------------
/**
 * IMPORTANT: The functions below are TEMPORARY placeholders to satisfy older imports.
 * They DO NOTHING except log a deprecation warning. They must not send messages,
 * wire sockets, or alter runtime behavior.
 */

function _warn(name) {
  try { console.warn(`[legacy stub] ${name}() is deprecated and a no-op.`); } catch {}
}

// Text senders
export function sendChat(/* text, ctx */)          { _warn("sendChat"); }
export function sendText(/* text, ctx */)          { _warn("sendText"); }
export function sendTextAndContext(/* text, ctx */){ _warn("sendTextAndContext"); }
export function sendMessage(/* text, ctx */)       { _warn("sendMessage"); }

// Socket/boot
export function attachWS(/* ws */)                 { _warn("attachWS"); }
export function setWS(/* ws */)                    { _warn("setWS"); }
export async function initChat(/* ... */)          { _warn("initChat"); }
export async function init(/* ... */)              { _warn("init"); }

// Stop/interrupt
export function stop(/* ... */)                    { _warn("stop"); }

// Message handler aliases
export function handleOnceResponse(/* evt */)      { _warn("handleOnceResponse"); }
export function handleWsMessage(/* evt */)         { _warn("handleWsMessage"); }
export function handleVoiceResponseOnce(/* evt */) { _warn("handleVoiceResponseOnce"); }

// Misc lanes
export function wireChatLane(/* ... */)            { _warn("wireChatLane"); }

// --------------------------- Default export ---------------------------
const _default = {
  // New API
  start,
  attachSocket,
  handleVoiceOnceResponse,
  sendUserText,
  interrupt,
  setTTSPlayer,
  // Legacy stubs (for consumers using default)
  sendChat,
  sendText,
  sendTextAndContext,
  sendMessage,
  attachWS,
  setWS,
  initChat,
  init,
  stop,
  handleOnceResponse,
  handleWsMessage,
  handleVoiceResponseOnce,
  wireChatLane
};
export default _default;

// --------------------------- Global exposure ---------------------------
window.ChatSend = _default;
