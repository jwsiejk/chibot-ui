/**
 * ws.js — WebSocket helpers for Ask Chip
 * Updated: 2025-09-08 (compat catch(e){} + robust reconnect)
 */

import { API, TIMING } from "./config.js";
import { getSID } from "./util/sid.js";
import { setState, STATES } from "./state.js";
import { showError } from "./errors.js";
import { playStream, stopPlayback, isPlaying } from "./audio.js";
import { renderSuggestions } from "./suggestions.js";

let ws = null;
let _openPromise = null;

// Stable per-tab id
let _tabId = null;
function getTabId() {
  if (_tabId) return _tabId;
  try {
    _tabId = sessionStorage.getItem("chip.tab");
    if (!_tabId) {
      _tabId = (crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : String(Date.now()) + Math.random().toString(16).slice(2);
      sessionStorage.setItem("chip.tab", _tabId);
    }
  } catch (e) {
    _tabId = "tab";
  }
  return _tabId;
}

// Reconnect policy
let reconnects = 0;
const MAX_RECONNECTS = Number.POSITIVE_INFINITY; // keep trying
const BASE_DELAY_MS = 800;                        // base backoff

function scheduleReconnect() {
  if (reconnects >= MAX_RECONNECTS) return;
  const delay = Math.min(30000, BASE_DELAY_MS * (2 ** reconnects)); // cap 30s
  reconnects++;
  setTimeout(() => openWS(), delay);
}

// Optional UI buttons (wired by app.js)
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
    if (endBtn)   endBtn.disabled   = !active;
  } catch (e) {}
}

// Heartbeat
let _hbTimer = null;
function startHeartbeat() {
  stopHeartbeat();
  const sendPing = () => {
    try {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    } catch (e) {}
  };
  sendPing();
  _hbTimer = setInterval(sendPing, 25000);
}
function stopHeartbeat() {
  try { if (_hbTimer) clearInterval(_hbTimer); } catch (e) {}
  _hbTimer = null;
}

// Turn state
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
  return xs; // passthrough
}

// Core WS handler
function onWSMessage(ev) {
  try {
    const msg = JSON.parse(ev.data);

    if (msg.type === "assistant_chunk" || msg.type === "text") {
      setState(STATES.RESPONDING);
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;
      const piece = msg.text || msg.content || "";
      if (piece) {
        try { addChatMessage("assistant", piece); } catch (e) {}
      }
    }

    if (msg.type === "audio_chunk") {
      if (msg.base64) _audioBufs.push(b64ToArrayBuffer(msg.base64));
    }

    if (msg.type === "audio_flush") {
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
      } catch (e) {}
      _audioBufs = [];
    }

    if (msg.type === "assistant_end" || msg.type === "end") {
      if (Array.isArray(msg.suggestions) && msg.suggestions.length) {
        try { renderSuggestions(msg.suggestions); } catch (e) {}
      }
      if (!isPlaying()) setState(STATES.READY);
      scheduleNudge();
    }

    if (msg.type === "state") {
      // map phases if desired
    }
  } catch (e) {
    // swallow parse errors; keep socket alive
  }
}

// Public controls
export function sendInterrupt() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const frame = { type: "control", cmd: "interrupt", turn_id: lastAssistantTurn };
  try { ws.send(JSON.stringify(frame)); } catch (e) {}
  stopPlayback();
  setState(STATES.LISTENING);
}

export function scheduleNudge() {
  if (nudgeTimer) clearTimeout(nudgeTimer);
  const ms = TIMING?.NUDGE_DELAY_MS ?? 4200;
  nudgeTimer = setTimeout(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: "control", cmd: "nudge" })); } catch (e) {}
  }, ms);
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
      reconnects = 0;          // reset on successful open
      resolve();
    };
  });

  ws.onmessage = onWSMessage;

  ws.onerror = () => {
    try {
      ws.close();              // funnel to onclose for reconnect
    } catch (e) {}
    showError(API.WS, "WS", "socket error");
  };

  ws.onclose = () => {
    updateButtons();
    stopHeartbeat();
    scheduleReconnect();
  };

  return ws;
}

export function closeWS() {
  try { if (ws) ws.close(); } catch (e) {}
  ws = null;
  stopHeartbeat();
  updateButtons();
}

// Promise: resolve when WS is OPEN
export function waitWSOpen(timeout = 4000) {
  if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve();
  const p = _openPromise || new Promise((res) => setTimeout(res, 10));
  if (!timeout) return p;
  return Promise.race([
    p,
    new Promise((_, rej) =>
      setTimeout(() => rej(new Error("ws_open_timeout")), timeout)
    ),
  ]);
}

// Reconnect on tab visible or network back
try {
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      try { openWS(); } catch (e) {}
    }
  });
  window.addEventListener("online", () => {
    try { openWS(); } catch (e) {}
  });
} catch (e) {}
