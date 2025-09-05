import { API, TIMING } from "./config.js";
import { getSID } from './util/sid.js';
import { setState, STATES } from "./state.js";
import { showError } from "./errors.js";
import { playStream, stopPlayback, isPlaying } from "./audio.js";
import { renderSuggestions } from "./suggestions.js";

let ws = null;
let reconnects = 0;
let startBtn, endBtn;
let nudgeTimer = null;
let lastAssistantTurn = null;

// Accumulators for current turn
let _audioBufs = [];
let _visemes = [];

export function bindControls(startEl, endEl){
  startBtn = startEl; endBtn = endEl;
  updateButtons();
}

export function openWS(){
  if (ws && ws.readyState === WebSocket.OPEN) return ws;
  ws = new WebSocket(API.WS);
  reconnects = 0;
  updateButtons();
  ws.onopen = () => { updateButtons(); };
  ws.onmessage = onWSMessage;
  ws.onerror = () => { showError(API.WS, "WS", "socket error"); };
  ws.onclose = () => {
    updateButtons();
    if (reconnects < 1){
      reconnects++;
      setTimeout(() => openWS(), 1000);
    }
  };
  return ws;
}

export function closeWS(){
  if (ws) try { ws.close(1000, "End"); } catch{};
  ws = null;
  updateButtons();
}

function updateButtons(){
  const active = ws && ws.readyState === WebSocket.OPEN;
  if (startBtn) startBtn.disabled = active;
  if (endBtn) endBtn.disabled = !active;
}

function b64ToArrayBuffer(b64){
  try{
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }catch(e){ return new ArrayBuffer(0); }
}

function normalizeVisemes(items){
  // Accept {t_ms, v} or {t, v}; output {t:ms, v}
  return (items||[]).map(x => ({
    t: typeof x.t !== "undefined" ? x.t : (x.t_ms ?? 0),
    v: x.v
  }));
}

function onWSMessage(ev){
  try{
    const msg = JSON.parse(ev.data);

    // Normalize both server frame dialects
    if (msg.type === "assistant_chunk" || msg.type === "text"){
      setState(STATES.RESPONDING);
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;
      // (optional) update chat UI here with msg.text or msg.content
    }

    if (msg.type === "audio_chunk"){
      if (msg.base64) _audioBufs.push(b64ToArrayBuffer(msg.base64));
    }

    if (msg.type === "visemes"){
      _visemes = normalizeVisemes(msg.items || msg.visemes || []);
    }

    if (msg.type === "suggestions"){
      if (Array.isArray(msg.items)) renderSuggestions(msg.items);
    }

    if (msg.type === "assistant_end" || msg.type === "end"){
      const bufs = _audioBufs.slice();
      const ves  = _visemes.slice();
      _audioBufs.length = 0;
      _visemes = [];
      setState(STATES.RESPONDING);
      playStream(bufs, ves).finally(() => setState(STATES.READY));
      // If this frame carries suggestions in 'suggestions' or 'items'
      const sug = msg.suggestions || msg.items;
      if (Array.isArray(sug)) renderSuggestions(sug);
      scheduleNudge();
      lastAssistantTurn = msg.turn_id || lastAssistantTurn;
    }

    if (msg.type === "error"){
      showError(API.WS, msg.code || "ERR", msg.message || "");
    }

    if (msg.type === "state"){
      // could map phases to UI if useful
    }

  }catch(e){}
}

export function sendInterrupt(){
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const turn = lastAssistantTurn;
  stopPlayback();
  const frame = { type: "cmd", cmd: "interrupt", turn_id: turn };
  ws.send(JSON.stringify(frame));
  setState(STATES.LISTENING);
}

function scheduleNudge(){
  if (nudgeTimer) clearTimeout(nudgeTimer);
  nudgeTimer = setTimeout(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type:"cmd", cmd:"nudge" }));
  }, TIMING.NUDGE_DELAY_MS);
}

export function cancelNudge(){
  if (nudgeTimer) { clearTimeout(nudgeTimer); nudgeTimer = null; }
}