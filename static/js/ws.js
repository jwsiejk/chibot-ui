/**
 * ws.js — WebSocket helpers for Ask Chip
 * Updated: 2025-09-07
 *
 * Behavior:
 *  - Opens a single WS to /ws/v1/chat with session_id + tab id
 *  - Auto-reconnects with backoff on close/error (survives deploys/rotations)
 *  - Streams assistant frames (text/audio/visemes/state/suggestions)
 *  - Exposes start/end controls, interrupts, and nudge helpers
 */

import { API, TIMING } from "./config.js";
import { getSID } from "./util/sid.js";
import { setState, STATES } from "./state.js";
import { showError } from "./errors.js";
import { playStream, stopPlayback, isPlaying } from "./audio.js";
import { renderSuggestions } from "./suggestions.js";

let ws = null;
let _openPromise = null;

// Tab identity (stable per-tab so server can correlate)
let _tabId = null;
function getTabId() {
  if (_tabId) return _tabId;
  try {
    _tabId = sessionStorage.getItem("chip.tab");
    if (!_tabId) {
      _tabId =
        (crypto && crypto.randomUUID)
          ? crypto.randomUUID()
          : String(Date.now()) + Math.random().toString(16).slice(2);
      sessionStorage.setItem("chip.tab", _tabId);
    }
  } catch {
    _tabId = "tab";
  }
  return _tabId;
}

// Reconnect backoff
let reconnects = 0;
const MAX_RECONNECTS = 8;
const BASE_DELAY_MS = 500;

function scheduleReconnect() {
  if (reconnects >= MAX_RECONNECTS) return;
  const delay = Math.min(10000, BASE_DELAY_MS * (2 ** reconnects));
  reconnects++;
  setTimeout(() => openWS(), delay);
}

// UI Buttons (optional wiring from app.js)
let startBtn, endBtn;
export function bindControls(startEl, endEl) {
  startBtn = startEl;
  endBtn = endEl;
  updateButtons();
}

function updateButtons() {
  const active = ws && ws.readyState === WebSocket.OPEN;
  try {
    if (startBtn) startBtn.disabled = active;
    if (endBtn) endBtn.disabled = !active;
  } catch {}
}

// Heartbeat (keep socket fresh + detect half-open)
let _hbTimer = null;
function startHeartbeat() {
  stopHeartbeat();
  const sendPing = () => {
    try {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    } catch {}
  };
  sendPing();
  _hbTimer = setInterval(sendPing, 25000);
}
function stopHeartbeat() {
  try {
    if (_hbTimer) clearInterval(_hbTimer);
  } catch {}
  _hbTimer = null;
}

// Chat flow state
let lastAssistantTurn = null;
let _audioBufs = [];
let nudgeTimer = null;

function b64ToArrayBuffer(base64) {
  const bin = atob(base64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function normalizeVisemes(items) {
  const xs = Array.isArray(items) ? items : [];
  // passthrough for now — keep placeholder to evolve format
  return xs;
}

// Core message handler
function onWSMessage(ev) {
  try {
    const msg = JSON.parse(ev.data);

    // Normalize server dialects
    if (msg.type === "assistant_chunk" || msg.type === "text") {
      setState(STATES.RESPONDING);
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;

      const piece = msg.text || msg.content || "";
      if (piece) {
        // Append assistant text to chat (app.js defines addChatMessage globally)
        try { addChatMessage("assistant", piece); } catch {}
      }
    }

    if (msg.type === "audio_chunk") {
      if (msg.base64) _audioBufs.push(b64ToArrayBuffer(msg.base64));
    }

    if (msg.type === "audio_flush") {
      // Play accumulated audio
      try {
        const total = _audioBufs.length
          ? _audioBufs.reduce((acc, buf) => {
              const a = new Uint8Array(acc);
              const b = new Uint8Array(buf);
              const out = new Uint8Array(a.length + b.length);
              out.set(a, 0); out.set(b, a.length);
              return out.buffer;
            })
          : null;
        if (total) playStream(total, normalizeVisemes(msg.visemes || []));
      } catch {}
      _audioBufs = [];
    }

    if (msg.type === "assistant_end" || msg.type === "end") {
      // Suggestions (chips)
      if (Array.isArray(msg.suggestions) && msg.suggestions.length) {
        try { renderSuggestions(msg.suggestions); } catch {}
      }
      if (!isPlaying()) setState(STATES.READY);
      scheduleNudge(); // start idle nudge timer
    }

    if (msg.type === "state") {
      // Map phases if you want visible UI states; currently no-op
      // phases: ready/listening/responding etc.
    }
  } catch (e) {
    // Parsing or unexpected payload — keep socket alive
  }
}

// Public controls
export function sendInterrupt() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const turn = lastAssistantTurn;
  const frame = { type: "control", cmd: "interrupt", turn_id: turn };
  try { ws.send(JSON.stringify(frame)); } catch {}
  stopPlayback();
  setState(STATES.LISTENING);
}

export function scheduleNudge() {
  if (nudgeTimer) clearTimeout(nudgeTimer);
  nudgeTimer = setTimeout(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: "control", cmd: "nudge" })); } catch {}
  }, TIMING.NUDGE_DELAY_MS || 4200);
}

export function cancelNudge() {
  if (nudgeTimer) { clearTimeout(nudgeTimer); nudgeTimer = null; }
}

// Open / Close
export function openWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return ws;

  ws = new WebSocket(
    `${API.WS}?session_id=${encodeURIComponent(getSID())}&tab=${encodeURIComponent(getTabId())}`
  );

  updateButtons();
  _openPromise = new Promise((resolve) => {
    ws.onopen = () => {
      updateButtons();
      startHeartbeat();
      reconnects = 0;               // reset on successful open
      resolve();
    };
  });

  ws.onmessage = onWSMessage;

  ws.onerror = () => {
    // Surface the error but funnel through onclose → reconnect path
    showError(API.WS, "WS", "socket error");
    try { ws.close(); } catch {}
  };

  ws.onclose = () => {
    updateButtons();
    stopHeartbeat();
    scheduleReconnect();
  };

  return ws;
}

export function closeWS() {
  try { if (ws) ws.close(); } catch {}
  ws = null;
  stopHeartbeat();
  updateButtons();
}

// Promise that resolves once WS is OPEN
export function waitWSOpen(timeout = 4000) {
  if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
  const p = _openPromise || new Promise((res) => setTimeout(res, 10));
  if (!timeout) return p;
  return Promise.race([
    p,
    new Promise((_, rej) => setTimeout(() => rej(new Error("ws_open_timeout")), timeout)),
  ]);
}
