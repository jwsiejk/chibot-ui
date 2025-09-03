
/**
 * send.js — MAX COMPAT build with soft / echo‑aware barge‑in
 *
 * Purpose: temporarily restore legacy named exports that older code may import
 * while still providing the new soft‑barge‑in behavior.
 *
 * New core API (preferred):
 *   - start()
 *   - attachSocket(ws)
 *   - handleVoiceOnceResponse(evtOrMsg)
 *   - sendUserText(text, ctx?)
 *   - interrupt(reason?)
 *   - setTTSPlayer(player)
 *
 * Legacy aliases re‑exported for compatibility (safe to remove later):
 *   - sendChat(text, ctx?)                 -> sendUserText
 *   - sendText(text, ctx?)                 -> sendUserText
 *   - sendTextAndContext(text, ctx?)       -> sendUserText
 *   - sendMessage(text, ctx?)              -> sendUserText
 *   - attachWS(ws) / setWS(ws)             -> attachSocket
 *   - initChat() / init()                  -> start
 *   - stop()                               -> interrupt('stop')
 *   - handleOnceResponse(evt)              -> handleVoiceOnceResponse
 *   - handleWsMessage(evt)                 -> handleVoiceOnceResponse
 *   - handleVoiceResponseOnce(evt)         -> handleVoiceOnceResponse
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

// --------------------------- Core API ---------------------------

export async function start() {
  if (started) return;
  started = true;

  // Prepare audio player
  ttsPlayer = window.ttsPlayer || createFallbackTTSPlayer();

  // Arm VAD (request mic permissions when you actually start the session)
  try {
    await VAD.arm();
  } catch (e) {
    console.warn("VAD arm failed (mic permissions?):", e);
  }

  // Instantiate soft barge-in controller; socket will be attached later via attachSocket()
  barge = new SoftBargeIn({
    vad: VAD,
    socket: null, // set later
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
    // Text vs binary
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
        if (barge) barge.onAssistantAudioStart();
        (ttsPlayer || (ttsPlayer = createFallbackTTSPlayer())).appendChunk(buf);
        return;
      }
    }
  } else if (evtOrMsg && typeof evtOrMsg === "object") {
    // Already a parsed message
    msg = evtOrMsg;
  }

  if (maybeWS && !wsRef) {
    attachSocket(maybeWS); // so barge can send 'interrupt' commands
  }

  if (!msg) return;

  switch (msg.type) {
    case "state":
      emitState(msg.state);
      break;

    case "text":
      // Bubble assistant/user text up to the UI
      try { window.dispatchEvent(new CustomEvent("chip:text", { detail: msg })); } catch {}
      break;

    case "audio_chunk": {
      if (barge) barge.onAssistantAudioStart();
      let buf;
      if (typeof msg.data === "string") {
        buf = base64ToArrayBuffer(msg.data);
      } else if (msg.data?.type === "Buffer" && Array.isArray(msg.data.data)) {
        buf = new Uint8Array(msg.data.data).buffer;
      } else if (Array.isArray(msg.data)) {
        buf = new Uint8Array(msg.data).buffer;
      } else {
        break;
      }
      (ttsPlayer || (ttsPlayer = createFallbackTTSPlayer())).appendChunk(buf);
      break;
    }

    case "end":
      if (barge) barge.onAssistantAudioEnd();
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

// --------------------------- Legacy Compatibility Exports ---------------------------

// Text senders
export function sendChat(text, ctx = {}) { return sendUserText(text, ctx); }
export function sendText(text, ctx = {}) { return sendUserText(text, ctx); }
export function sendTextAndContext(text, ctx = {}) { return sendUserText(text, ctx); }
export function sendMessage(text, ctx = {}) { return sendUserText(text, ctx); }

// Socket/boot
export function attachWS(ws) { return attachSocket(ws); }
export function setWS(ws) { return attachSocket(ws); }
export async function initChat() { return start(); }
export async function init() { return start(); }

// Stop/interrupt
export function stop() { return interrupt("stop"); }

// Message handlers
export function handleOnceResponse(evt) { return handleVoiceOnceResponse(evt); }
export function handleWsMessage(evt) { return handleVoiceOnceResponse(evt); }
export function handleVoiceResponseOnce(evt) { return handleVoiceOnceResponse(evt); }

// Default export (optional consumers)
const _default = {
  start,
  attachSocket,
  handleVoiceOnceResponse,
  sendUserText,
  sendChat,
  sendText,
  sendTextAndContext,
  sendMessage,
  interrupt,
  setTTSPlayer,
  attachWS,
  setWS,
  initChat,
  init,
  stop,
  handleOnceResponse,
  handleWsMessage,
  handleVoiceResponseOnce
};
export default _default;

// --------------------------- Global exposure ---------------------------
window.ChatSend = _default;

/* ------------------------------------------------------------------
 * Legacy no-op exports (temporary)
 * ------------------------------------------------------------------
 * These functions exist only to satisfy older imports so the app loads.
 * They should NOT be used by the new Ask Chip runtime. They do nothing.
 * Please remove these once main.js is updated to use the new API.
 */

export function sendChat(/* text, ctx */) {
  try { console.warn("[legacy stub] sendChat() is deprecated and a no-op."); } catch {}
  // Intentionally no-op
}

export function wireChatLane(/* ...args */) {
  try { console.warn("[legacy stub] wireChatLane() is deprecated and a no-op."); } catch {}
  // Intentionally no-op
}

