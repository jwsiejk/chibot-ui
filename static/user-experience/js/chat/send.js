
/**
 * send.js — full file (updated)
 *
 * Implements soft / echo‑aware barge‑in while preserving normal streaming behavior.
 * This module wires:
 *   - WebSocket chat stream (/ws/v1/chat)
 *   - TTS audio playback (generic adapter)
 *   - VAD (voice activity) with echo-aware thresholds
 *   - Soft barge‑in controller (pause → confirm → commit)
 *
 * Integration expectations:
 *   - Place alongside this path: static/user-experience/js/chat/send.js
 *   - Ensure 'soft-bargein.js' is in the same folder, and 'voice/vad.js' exists.
 *   - Provide a ttsPlayer globally (window.ttsPlayer) OR let the lightweight fallback work.
 *   - Dispatch a 'chip:start' event to begin; Esc key triggers immediate interrupt.
 *
 * UI hooks (optional):
 *   - CSS class 'chip-paused-pending' is toggled during tentative pause.
 *   - We emit window events 'chip:state' with values: ready|listening|responding|thinking.
 */

import { SoftBargeIn } from "./soft-bargein.js";
import * as VAD from "../voice/vad.js";

(function () {
  "use strict";

  // --------------------------- Config ---------------------------

  const WS_URL = window.CHIP_WS_URL || (location.origin.replace(/^http/, 'ws') + "/ws/v1/chat");
  const BARGE_CONFIRM_MS = 420;     // Confirm duration for soft barge-in
  const ECHO_THRESHOLD_BOOST = 1.9; // Higher = more resistant to speaker echo
  const AUTO_ARM_VAD = false;       // true: arm on load; false: arm on 'chip:start'

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
        // data can be ArrayBuffer or Uint8Array
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

  // --------------------------- Main boot ---------------------------

  let ws = null;
  let ttsPlayer = null;
  let barge = null;
  let started = false;

  async function start() {
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

    // Open WebSocket
    ws = new WebSocket(WS_URL);

    ws.addEventListener("open", () => {
      emitState("ready");
    });

    // Instantiate soft barge-in controller and wire to VAD/WS/player
    barge = new SoftBargeIn({
      vad: VAD,
      socket: ws,
      ttsPlayer,
      confirmMs: BARGE_CONFIRM_MS,
      echoThresholdBoost: ECHO_THRESHOLD_BOOST,
      onPendingUI: (isPending) => {
        document.body.classList.toggle("chip-paused-pending", !!isPending);
      },
      interruptCmd: "interrupt"
    });
    barge.wire();

    // WS message handling
    ws.addEventListener("message", async (evt) => {
      try {
        if (typeof evt.data === "string") {
          const msg = JSON.parse(evt.data);

          switch (msg.type) {
            case "state":
              emitState(msg.state);
              break;

            case "text":
              // Bubble the assistant/user text up to the UI
              window.dispatchEvent(new CustomEvent("chip:text", { detail: msg }));
              break;

            case "audio_chunk": {
              // Expect either base64 'data' or an Array<number>
              barge.onAssistantAudioStart();
              let buf;
              if (typeof msg.data === "string") {
                buf = base64ToArrayBuffer(msg.data);
              } else if (msg.data?.type === "Buffer" && Array.isArray(msg.data.data)) {
                buf = new Uint8Array(msg.data.data).buffer;
              } else if (Array.isArray(msg.data)) {
                buf = new Uint8Array(msg.data).buffer;
              } else {
                // Unknown format; ignore silently
                break;
              }
              ttsPlayer.appendChunk(buf);
              break;
            }

            case "end":
              barge.onAssistantAudioEnd();
              ttsPlayer.finalize();
              emitState("ready");
              break;

            case "error":
              console.error("WS error msg:", msg);
              break;

            default:
              // no-op
              break;
          }
        } else {
          // Binary payload path (Blob/ArrayBuffer) — treat as audio chunk
          const data = evt.data;
          let buf;
          if (data instanceof ArrayBuffer) {
            buf = data;
          } else if (data instanceof Blob) {
            buf = await data.arrayBuffer();
          }
          if (buf) {
            barge.onAssistantAudioStart();
            ttsPlayer.appendChunk(buf);
          }
        }
      } catch (err) {
        console.error("WS message handling failed:", err);
      }
    });

    ws.addEventListener("close", () => {
      emitState("ready");
      try { barge?.unwire?.(); } catch {}
    });

    ws.addEventListener("error", (e) => {
      console.error("WS error:", e);
    });
  }

  // --------------------------- Outbound helpers ---------------------------

  function sendUserText(text, ctx = {}) {
    if (!ws || ws.readyState !== 1) return;
    const payload = { type: "user", mode: "text", text, ctx };
    try { ws.send(JSON.stringify(payload)); } catch {}
  }

  // Optional: allow other parts to request an immediate interrupt
  function interrupt(reason = "manual") {
    try { barge?.immediateInterrupt?.(reason); } catch {}
  }

  // --------------------------- Event wiring ---------------------------

  // Start when the app signals readiness (matches your profile‑gated flow).
  window.addEventListener("chip:start", start);

  // Quick manual override: ESC to interrupt immediately.
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") interrupt("keyboard");
  });

  // Optional auto-start (disabled by default)
  if (AUTO_ARM_VAD) {
    // Pre-arm the VAD; actual start still occurs on 'chip:start'.
    VAD.arm().catch(() => {});
  }
  if (window.CHIP_AUTOSTART) {
    start().catch(() => {});
  }

  // Expose a minimal API for other modules
  window.ChatSend = {
    start,
    sendUserText,
    interrupt
  };
})();
